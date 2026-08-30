"""D4 端到端测试：真实跑任务流水线 + 人类待办 + 验收，然后检查看板 REST API 数据一致。"""
from __future__ import annotations

import datetime
import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

from laoban.dashboard.server import DashboardServer
from laoban.core.store import JsonStore
from laoban.core.task import Task, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, WAITING_HUMAN, DONE, REPORTING
from laoban.core.state_machine import advance
from laoban.core.employee import Employee
from laoban.core.human_inbox import HumanInbox
from laoban.core.ledger import Ledger
from laoban.core.feedback import write_back_experience


class TestD4DashboardConsistency(unittest.TestCase):
    """D4：看板数据与任务实际流转一致（时间线、绩效统计无错）。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        store = JsonStore(cls.root)

        # 员工
        store.save_employee(Employee(id="dev", name="阿码"))
        store.save_employee(Employee(id="pm", name="老谋"))
        store.save_employee(Employee(id="emp-chen", name="陈工", kind="human",
                                     department="dev_dept"))
        # 任务流水线（含 WAITING_HUMAN → 恢复 → DONE）
        t1 = Task(id="T-D4-01", title="写清洗函数")
        for state, actor in [(TRIAGE, "pm"), (PLANNING, "dev"),
                              (REVIEW, "dev"), (ASSIGNED, "pm"),
                              (DOING, "dev"), (WAITING_HUMAN, "dev"),
                              (DOING, "emp-chen"), (REPORTING, "dev"), (DONE, "pm")]:
            advance(t1, state, actor=actor)
        store.save_task(t1)

        # 第二个任务（还在 doing）
        t2 = Task(id="T-D4-02", title="写文档")
        advance(t2, TRIAGE, actor="pm"); advance(t2, PLANNING, actor="dev"); store.save_task(t2)

        # 人类待办
        inbox = HumanInbox(store)
        inbox.create(task_id="T-D4-01", title="配合核查异常值", assignee="emp-chen",
                     due_date=datetime.date.today().isoformat())

        # 绩效 & 经验
        ledger = Ledger()
        ledger.record_completion("dev", task_id="T-D4-01", cost=1.23, elapsed=300)
        ledger.record_rejection("dev")
        ledger.record_human_intervention("dev", "human_task")

        dev = store.load_employee("dev")
        write_back_experience(dev, task_type="feature", score=4, comment="善用人类协作")
        store.save_employee(dev)

        cls.server = DashboardServer(store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_api_tasks_count_and_states(self):
        with urllib.request.urlopen(self.base + "/api/tasks") as r:
            data = json.loads(r.read())
        self.assertEqual(len(data), 2)
        by = {d["id"]: d for d in data}
        self.assertEqual(by["T-D4-01"]["state"], "done")
        self.assertEqual(by["T-D4-02"]["state"], "planning")

    def test_task_flow_log_reflects_real_transitions(self):
        """D4：时间线无错——DONE 的任务 flow_log 必须含 9 次真实流转。"""
        with urllib.request.urlopen(self.base + "/api/tasks") as r:
            data = json.loads(r.read())
        done = [d for d in data if d["id"] == "T-D4-01"][0]
        actors = [log["actor"] for log in done["flow_log"]]
        # 9 次流转顺序
        self.assertEqual(actors, ["pm", "dev", "dev", "pm", "dev", "dev", "emp-chen", "dev", "pm"])
        self.assertEqual(done["flow_log"][-1]["from"], "reporting")
        self.assertEqual(done["flow_log"][-1]["to"], "done")

    def test_api_employees_include_human_and_memory(self):
        with urllib.request.urlopen(self.base + "/api/employees") as r:
            data = json.loads(r.read())
        by = {d["id"]: d for d in data}
        self.assertEqual(by["emp-chen"]["kind"], "human")
        # 经验回写后的记忆必须通过看板 API 露出
        self.assertEqual(by["dev"]["memory"]["experiences"][0]["outcome"], "success")
        self.assertIn("善用人类协作",
                      by["dev"]["memory"]["experiences"][0]["learned"])

    def test_api_human_tasks_today_matches_inbox(self):
        who = urllib.parse.quote("emp-chen")
        date = datetime.date.today().isoformat()
        with urllib.request.urlopen(f"{self.base}/api/human-tasks?who={who}&date={date}") as r:
            items = json.loads(r.read())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["assignee"], "emp-chen")
        self.assertEqual(items[0]["status"], "pending")

    def test_nonexistent_human_has_empty_list(self):
        who = urllib.parse.quote("不存在的人")
        date = datetime.date.today().isoformat()
        with urllib.request.urlopen(f"{self.base}/api/human-tasks?who={who}&date={date}") as r:
            items = json.loads(r.read())
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
