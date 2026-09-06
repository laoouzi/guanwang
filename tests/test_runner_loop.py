import tempfile
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task
from laoban.core.messenger import inbox
from laoban.core.human_inbox import HumanInbox
from laoban.llm.base import Message
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM
from laoban.runner.runner import Runner

TOOL_CALL = """我需要陈工帮忙核查数据。

[TOOL] delegate_task
{"assignee": "emp-chen", "title": "核查三份样本数据异常值", "instruction": "核对后回传", "due": "2026-08-30"}
[/TOOL]
"""

FINAL = "数据清洗函数已完成，人类核查已并行派发。"


class RecordingLLM:
    """记录收到的 messages，按脚本返回响应（验证 prompt 组装）。"""

    def __init__(self, responses: list[str]):
        self._responses = responses
        self._idx = 0
        self.captured: list[list[Message]] = []

    def chat(self, messages, tools=None):
        from laoban.llm.base import LLMResponse
        self.captured.append(list(messages))
        r = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return LLMResponse(content=r)


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(
        id="dev", name="阿码", model_config={"provider": "dev"},
        permissions={"can_assign_human_tasks": True}))
    st.save_employee(Employee(
        id="emp-chen", name="陈工", kind="human", title="数据核查员"))
    st.save_employee(Employee(id="dev2", name="阿码二号"))
    return st


class TestRunnerToolLoop(unittest.TestCase):
    def setUp(self):
        self.store = _mk_store()

    def test_no_store_backward_compat(self):
        # 不传 store：行为与旧版一致（无工具循环）
        gw = LLMGateway()
        gw.register_mock("dev", MockLLM(responses=["[dev] 完成"]))
        r = Runner(gw)
        emp = self.store.load_employee("dev")
        out = r.run(emp, Task(id="T-1", title="x"))
        self.assertEqual(out, "[dev] 完成")

    def test_directory_injected_into_prompt(self):
        llm = RecordingLLM(["完成"])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        r = Runner(gw, store=self.store)
        dev = self.store.load_employee("dev")
        r.run(dev, Task(id="T-1", title="x"))
        system = llm.captured[0][0].content
        self.assertIn("组织通讯录", system)
        self.assertIn("emp-chen", system)          # 人类同事可见
        self.assertIn("数据核查员", system)
        self.assertNotIn("dev 阿码", system)        # 排除自己
        self.assertIn("[TOOL]", system)             # 工具协议说明

    def test_tool_call_executed_and_final_returned(self):
        llm = RecordingLLM([TOOL_CALL, FINAL])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        r = Runner(gw, store=self.store)
        dev = self.store.load_employee("dev")
        out = r.run(dev, Task(id="T-1", title="x"))
        # 最终交付是第二轮的文本
        self.assertIn(FINAL.split("。")[0], out)
        # 工具真实生效：人类待办落库
        pending = HumanInbox(self.store).list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].assignee, "emp-chen")
        self.assertEqual(pending[0].created_by, "dev")
        # 协作动作附在产出尾部（可审计）
        self.assertIn("协作动作", out)
        self.assertIn("delegate_task", out)

    def test_tool_result_fed_back(self):
        llm = RecordingLLM([TOOL_CALL, FINAL])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        r = Runner(gw, store=self.store)
        dev = self.store.load_employee("dev")
        r.run(dev, Task(id="T-1", title="x"))
        # 第二轮对话里应有工具结果反馈
        second_round = llm.captured[1]
        self.assertTrue(any("工具执行结果" in m.content for m in second_round
                            if m.role == "user"))

    def test_send_message_via_tool(self):
        call = ('[TOOL] send_message\n{"to": "dev2", "content": "帮我复核"}\n[/TOOL]\n')
        llm = RecordingLLM([call, "已通知"])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        r = Runner(gw, store=self.store)
        r.run(self.store.load_employee("dev"), Task(id="T-1", title="x"))
        self.assertEqual(len(inbox(self.store, "dev2")), 1)

    def test_max_rounds_guard(self):
        # LLM 无限重复工具调用 → 循环上限兜底，不炸
        llm = RecordingLLM([TOOL_CALL])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        r = Runner(gw, store=self.store, max_tool_rounds=2)
        out = r.run(self.store.load_employee("dev"), Task(id="T-1", title="x"))
        self.assertIn("协作动作", out)
        self.assertLessEqual(len(llm.captured), 3)  # 初始 + 2 轮反馈

    def test_bad_json_tool_args_feedback(self):
        call = '[TOOL] send_message\n{不是json}\n[/TOOL]\n'
        llm = RecordingLLM([call, FINAL])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        r = Runner(gw, store=self.store)
        out = r.run(self.store.load_employee("dev"), Task(id="T-1", title="x"))
        self.assertIn(FINAL.split("。")[0], out)
        # 坏参数被拒绝并反馈，未发送任何消息
        self.assertEqual(inbox(self.store, "dev2"), [])
        self.assertIn("❌", out)

    def test_unknown_tool_feedback(self):
        call = '[TOOL] teleport\n{"x": 1}\n[/TOOL]\n'
        llm = RecordingLLM([call, FINAL])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        r = Runner(gw, store=self.store)
        out = r.run(self.store.load_employee("dev"), Task(id="T-1", title="x"))
        self.assertIn(FINAL.split("。")[0], out)
        self.assertIn("❌", out)


if __name__ == "__main__":
    unittest.main()
