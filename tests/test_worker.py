"""WorkerLoop（自动运转引擎）测试。

覆盖：
- 核心闭环：assigned 任务自动 doing → Runner 执行 → reporting（交付物落档）
- 仅 AI 自动执行：人类员工的 assigned 任务不被碰
- 幂等：reporting/done 状态跳过，重复 tick 无副作用
- 失败转 blocked：LLM 抛错 → 任务终态 blocked + block_reason 落原因
- 非在职员工（停职）跳过
- 账本记步（record_step）
- 线程启停（start/stop 不残留）
"""
from __future__ import annotations

import tempfile
import time
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task, ASSIGNED, DOING, REPORTING, DONE
from laoban.core.state_machine import advance
from laoban.core.workstation import assign_task_auto, queue_of
from laoban.core.ledger import FileLedger
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM
from laoban.runner.worker import WorkerLoop


class _FailingLLM:
    """永远抛错的假 provider。"""

    def chat(self, messages, tools=None):
        raise RuntimeError("LLM 服务不可用")


class _MeteredLLM:
    """按调用计 token 的假 provider：每次 chat 报固定 token 数。"""

    def __init__(self, tokens_per_call: int = 100):
        self.calls = 0
        self.tokens = tokens_per_call

    def chat(self, messages, tools=None):
        from laoban.llm.base import LLMResponse
        self.calls += 1
        return LLMResponse(content=f"交付第 {self.calls} 次", usage_tokens=self.tokens)


def _mk_store():
    st = JsonStore(tempfile.mkdtemp())
    st.save_employee(Employee(
        id="dev", name="阿码", kind="ai", department="dev_dept",
        model_config={"provider": "dev", "model": "mock"}))
    st.save_employee(Employee(
        id="emp-chen", name="陈工", kind="human", department="dev_dept"))
    st.save_employee(Employee(
        id="dev2", name="老码", kind="ai", department="dev_dept",
        model_config={"provider": "dev", "model": "mock"}))
    return st


def _mk_gw(responses=("自动交付内容 AUTO-DELIVER。",)):
    gw = LLMGateway()
    gw.register_provider("dev", MockLLM(responses=list(responses)))
    return gw


def _assign(st, tid, to="dev"):
    st.save_task(Task(id=tid, title=f"任务{tid}"))
    return assign_task_auto(st, tid, to)


class TestWorkerLoop(unittest.TestCase):

    def test_auto_flow_assigned_to_reporting(self):
        st = _mk_store()
        _assign(st, "T-1")
        loop = WorkerLoop(st, _mk_gw())
        results = loop.tick()
        self.assertEqual(results, [{"task_id": "T-1", "employee": "dev",
                                    "result": "reporting"}])
        t = st.load_task("T-1")
        self.assertEqual(t.state, REPORTING)
        # 交付物落档
        self.assertEqual(t.progress_log[-1]["deliverable"],
                         "自动交付内容 AUTO-DELIVER。")
        # flow_log：直派快捷 4 步 + doing + reporting
        tos = [log["to"] for log in t.flow_log]
        self.assertEqual(tos[-2:], [DOING, REPORTING])
        # 任务仍在队列（等人类验收出队）
        self.assertIn("T-1", queue_of(st, "dev"))
        # 账本记步
        self.assertEqual(loop.ledger.stats("dev")["completion_count"], 0)

    def test_human_tasks_not_touched(self):
        st = _mk_store()
        _assign(st, "T-H", to="emp-chen")
        loop = WorkerLoop(st, _mk_gw())
        results = loop.tick()
        self.assertEqual(results, [])   # 人类任务不自动执行
        self.assertEqual(st.load_task("T-H").state, ASSIGNED)

    def test_suspended_ai_skipped(self):
        st = _mk_store()
        _assign(st, "T-S", to="dev2")
        # 派单后停职：worker 不再执行其队列任务
        emp = st.load_employee("dev2")
        emp.status = "suspended"
        st.save_employee(emp)
        loop = WorkerLoop(st, _mk_gw())
        self.assertEqual(loop.tick(), [])
        self.assertEqual(st.load_task("T-S").state, ASSIGNED)

    def test_idempotent(self):
        st = _mk_store()
        _assign(st, "T-2")
        loop = WorkerLoop(st, _mk_gw())
        loop.tick()
        # 第二轮：任务已 reporting，跳过
        self.assertEqual(loop.tick(), [])
        self.assertEqual(st.load_task("T-2").state, REPORTING)
        # 验收完成后再 tick 也无副作用
        t = st.load_task("T-2")
        advance(t, DONE, actor="boss")
        st.save_task(t)
        from laoban.core.workstation import dequeue
        dequeue(st, "dev", "T-2")
        self.assertEqual(loop.tick(), [])

    def test_llm_failure_blocks_task(self):
        st = _mk_store()
        _assign(st, "T-F")
        gw = LLMGateway()
        gw.register_provider("dev", _FailingLLM())
        loop = WorkerLoop(st, gw)
        results = loop.tick()
        self.assertIn("blocked", results[0]["result"])
        t = st.load_task("T-F")
        self.assertEqual(t.state, "blocked")
        self.assertIn("LLM 服务不可用", t.block_reason)
        # blocked 是终态：再 tick 不会重试（避免失败风暴）
        self.assertEqual(loop.tick(), [])

    def test_llm_failure_dequeues_and_notifies_boss(self):
        """blocked 死单善后：出队不占工位 + 站内信通知老板。"""
        from laoban.core.messenger import inbox
        st = _mk_store()
        boss = Employee(id="boss", name="老板", kind="human")
        boss.permissions["role"] = "admin"
        st.save_employee(boss)
        _assign(st, "T-F2")
        gw = LLMGateway()
        gw.register_provider("dev", _FailingLLM())
        loop = WorkerLoop(st, gw)
        loop.tick()
        self.assertEqual(st.load_task("T-F2").state, "blocked")
        # 出队：终态任务不再被反复扫描
        self.assertNotIn("T-F2", queue_of(st, "dev"))
        # 老板收到执行失败通知（escalation 确定性兜底）
        box = inbox(st, "boss")
        self.assertTrue(any("T-F2" in m["content"] and "执行失败" in m["content"]
                            for m in box))

    def test_blocked_task_reexecutes_after_retry(self):
        """死单复活闭环：失败转 blocked → 复活回 assigned → worker 再跑成功。"""
        st = _mk_store()
        _assign(st, "T-RY")
        gw = LLMGateway()
        gw.register_provider("dev", _FailingLLM())
        loop = WorkerLoop(st, gw)
        loop.tick()
        self.assertEqual(st.load_task("T-RY").state, "blocked")
        # 老板重试：blocked → assigned（复用轮次）+ 重入队
        t = st.load_task("T-RY")
        advance(t, ASSIGNED, actor="boss", remark="重试")
        t.block_reason = ""
        st.save_task(t)
        from laoban.core.workstation import enqueue
        enqueue(st, "dev", "T-RY")
        self.assertEqual(t.review_round, 1)
        # 换回健康 provider，下一轮 tick 重跑成功
        loop.gateway = _mk_gw()
        loop.runner.gateway = loop.gateway
        results = loop.tick()
        self.assertEqual(results, [{"task_id": "T-RY", "employee": "dev",
                                    "result": "reporting"}])
        self.assertEqual(st.load_task("T-RY").state, REPORTING)

    def test_reworked_task_reexecutes(self):
        """驳回返工回炉的任务：回队列后 worker 再次自动执行。"""
        st = _mk_store()
        _assign(st, "T-RW")
        loop = WorkerLoop(st, _mk_gw())
        loop.tick()
        self.assertEqual(st.load_task("T-RW").state, REPORTING)
        # 模拟验收驳回返工（reporting → assigned，计 1 轮）
        t = st.load_task("T-RW")
        advance(t, ASSIGNED, actor="boss", remark="驳回返工")
        st.save_task(t)
        self.assertEqual(t.review_round, 1)
        # 下一轮 tick 自动重做
        results = loop.tick()
        self.assertEqual(results, [{"task_id": "T-RW", "employee": "dev",
                                    "result": "reporting"}])
        t = st.load_task("T-RW")
        self.assertEqual(t.state, REPORTING)
        self.assertEqual(t.review_round, 1)        # 返工轮次保留
        self.assertEqual(len(t.progress_log), 2)   # 两轮交付落档

    def test_thread_start_stop(self):
        st = _mk_store()
        _assign(st, "T-3")
        loop = WorkerLoop(st, _mk_gw(), interval=0.1)
        loop.start()
        # 等线程跑起来处理完
        for _ in range(50):
            if st.load_task("T-3").state == REPORTING:
                break
            time.sleep(0.05)
        loop.stop()
        self.assertEqual(st.load_task("T-3").state, REPORTING)

    def test_token_usage_settled_per_call(self):
        """token 按调用结算：共用 provider 的员工与旁路调用不串账。"""
        from laoban.llm.base import Message
        st = _mk_store()   # dev 与 dev2 共用 provider "dev"
        _assign(st, "T-A")
        _assign(st, "T-B", to="dev2")
        gw = LLMGateway()
        gw.register_provider("dev", _MeteredLLM(100))
        # 旁路调用（模拟评审/复盘/chat 走同一 provider）：
        # 旧「全局累计+读后清零」口径会把这 100 token 串进首个任务
        gw.chat("dev", [Message(role="user", content="旁路调用")])
        loop = WorkerLoop(st, gw)
        loop.tick()
        a = st.load_task("T-A").progress_log[-1]["usage_tokens"]
        b = st.load_task("T-B").progress_log[-1]["usage_tokens"]
        self.assertEqual(a, 100)   # 各算各的，只含自己那次运行
        self.assertEqual(b, 100)

    def test_runner_with_store_collab_context(self):
        """Runner 带 store：AI 能看到通讯录与留言（不炸即可）。"""
        st = _mk_store()
        _assign(st, "T-4")
        loop = WorkerLoop(st, _mk_gw())
        loop.tick()
        t = st.load_task("T-4")
        self.assertEqual(t.state, REPORTING)


if __name__ == "__main__":
    unittest.main()
