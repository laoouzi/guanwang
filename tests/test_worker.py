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
