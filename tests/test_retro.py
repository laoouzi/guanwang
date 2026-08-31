"""AI 复盘机制测试：验收 → 自动教训 → 下次执行生效。

覆盖：
- 高分 + 评语：learned = 评语（现状兼容），auto=False；
- 评语为空 / 低分：自动复盘（LLM 优先，模板降级），auto=True；
- LLM 失败 → 模板教训（复盘永不缺席）；
- render_experience：低分教训排前、空 learned 跳过、limit 截断；
- Runner system prompt 注入结构化经验（不再是 dict repr）；
- 验收端点返回 review 字段。
"""
from __future__ import annotations

import tempfile
import threading
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task
from laoban.core.state_machine import advance
from laoban.core.memory import render_experience
from laoban.core.retro import review_and_learn, LOW_SCORE
from laoban.llm.gateway import LLMGateway
from laoban.llm.base import Message, LLMResponse
from laoban.runner.runner import Runner
from tests.test_rbac import _Client, _mk_store


class _ScriptLLM:
    """按脚本回固定内容的假 LLM。"""

    def __init__(self, content: str):
        self.content = content
        self.calls: list[Message] = []

    def chat(self, messages, tools=None):
        self.calls.append(messages[0])
        return LLMResponse(content=self.content)


class _FailingLLM:
    def chat(self, messages, tools=None):
        raise RuntimeError("boom")


def _emp(kind="ai") -> Employee:
    return Employee(id="dev", name="阿码", kind=kind, department="dev_dept",
                    model_config={"provider": "p"})


def _task(with_deliverable=True) -> Task:
    t = Task(id="T-1", title="数据清洗", instruction="输出函数+单测")
    t.progress_log.append({"deliverable": "def clean(): pass",
                           "by": "dev", "at": ""})
    if not with_deliverable:
        t.progress_log.clear()
    return t


class TestReviewAndLearn(unittest.TestCase):

    def test_high_score_with_comment_uses_comment(self):
        emp = _emp()
        exp = review_and_learn(None, emp, _task(), score=5,
                               comment="很好，继续保持")
        self.assertEqual(exp["learned"], "很好，继续保持")
        self.assertFalse(exp["auto"])
        self.assertEqual(exp["outcome"], "success")

    def test_empty_comment_auto_template(self):
        emp = _emp()
        exp = review_and_learn(None, emp, _task(), score=4, comment="")
        self.assertTrue(exp["auto"])
        self.assertIn("验收 4/5 通过", exp["learned"])

    def test_low_score_forces_llm_review(self):
        emp = _emp()
        gw = LLMGateway()
        llm = _ScriptLLM("下次先写单测再交付，避免边界遗漏")
        gw.register_provider("p", llm)
        exp = review_and_learn(None, emp, _task(), score=LOW_SCORE,
                               comment="有 bug", gateway=gw)
        self.assertEqual(exp["learned"], "下次先写单测再交付，避免边界遗漏")
        self.assertTrue(exp["auto"])
        self.assertEqual(exp["outcome"], "failure")
        # 复盘 prompt 里带了任务要求与交付物
        self.assertIn("数据清洗", llm.calls[0].content)
        self.assertIn("def clean", llm.calls[0].content)

    def test_llm_failure_falls_back_to_template(self):
        emp = _emp()
        gw = LLMGateway()
        gw.register_provider("p", _FailingLLM())
        exp = review_and_learn(None, emp, _task(), score=1, gateway=gw)
        self.assertTrue(exp["auto"])
        self.assertIn("未达标", exp["learned"])

    def test_human_employee_uses_template(self):
        emp = _emp(kind="human")
        gw = LLMGateway()
        gw.register_provider("p", _ScriptLLM("不该出现"))
        exp = review_and_learn(None, emp, _task(), score=1, gateway=gw)
        self.assertNotEqual(exp["learned"], "不该出现")
        self.assertIn("未达标", exp["learned"])

    def test_lesson_truncated(self):
        emp = _emp()
        gw = LLMGateway()
        gw.register_provider("p", _ScriptLLM("长" * 500))
        exp = review_and_learn(None, emp, _task(), score=1, gateway=gw)
        self.assertLessEqual(len(exp["learned"]), 120)


class TestRenderExperience(unittest.TestCase):

    def _emp_with(self, exps):
        e = _emp()
        e.memory["experiences"] = exps
        return e

    def test_empty(self):
        self.assertEqual(render_experience(_emp()), "暂无")

    def test_failure_first_and_skip_empty(self):
        e = self._emp_with([
            {"outcome": "success", "learned": "模板可复用"},
            {"outcome": "failure", "learned": "先写单测"},
            {"outcome": "success", "learned": ""},
        ])
        text = render_experience(e)
        lines = text.splitlines()
        self.assertEqual(lines[0], "- [教训] 先写单测")
        self.assertEqual(lines[1], "- [经验] 模板可复用")
        self.assertEqual(len(lines), 2)

    def test_limit(self):
        exps = [{"outcome": "failure", "learned": f"教训{i}"} for i in range(8)]
        e = self._emp_with(exps)
        lines = render_experience(e, limit=3).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "- [教训] 教训5")

    def test_all_empty_learned(self):
        e = self._emp_with([{"outcome": "failure", "learned": ""}])
        self.assertEqual(render_experience(e), "暂无")


class TestRunnerInjectsExperience(unittest.TestCase):

    def test_system_prompt_has_structured_experience(self):
        emp = _emp()
        emp.memory["experiences"] = [
            {"outcome": "failure", "learned": "先对照要求自查"}]
        st = JsonStore(tempfile.mkdtemp())
        st.save_employee(emp)
        r = Runner(LLMGateway(), store=st)
        sys_prompt = r._system(emp)
        self.assertIn("- [教训] 先对照要求自查", sys_prompt)
        self.assertNotIn("{'experiences'", sys_prompt)   # 不再是 dict repr


class TestAcceptEndpointReview(unittest.TestCase):
    """验收端点：响应带 review 字段（复盘结果）。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        from laoban.dashboard.server import DashboardServer
        cls.server = DashboardServer(cls.store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.client = _Client(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_accept_returns_review(self):
        st = self.store
        dev = st.load_employee("dev")
        if dev is None:
            dev = Employee(id="dev", name="阿码", kind="ai",
                           department="dev_dept")
            st.save_employee(dev)
        from laoban.core.workstation import assign_task_auto
        t = Task(id=f"T-{id(self)%99999:05d}-r", title="复盘任务",
                 instruction="做点什么")
        t.progress_log.append({"deliverable": "交付内容", "by": "dev", "at": ""})
        st.save_task(t)
        assign_task_auto(st, t.id, "dev")
        t = st.load_task(t.id)
        advance(t, "doing", actor="test")
        advance(t, "reporting", actor="test")
        st.save_task(t)

        status, body = self.client.post("/api/task/accept",
                                        {"id": t.id, "score": 2})
        self.assertEqual(status, 200)
        self.assertIn("review", body)
        self.assertTrue(body["review"]["auto"])       # 低分 → 自动复盘
        self.assertIn("未达标", body["review"]["learned"])
        # 员工记忆已落账
        dev = st.load_employee("dev")
        self.assertEqual(dev.memory["experiences"][-1]["learned"],
                         body["review"]["learned"])


if __name__ == "__main__":
    unittest.main()
