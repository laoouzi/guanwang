import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

from laoban.dashboard.server import DashboardServer
from laoban.core.store import JsonStore
from laoban.core.task import Task, DOING
from laoban.core.employee import Employee
from laoban.core.human_inbox import HumanInbox


class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        store = JsonStore(cls.root)
        store.save_task(Task(id="T-1", title="写函数", state=DOING))
        store.save_employee(Employee(id="dev", name="阿码"))
        store.save_employee(Employee(id="emp-陈工", name="陈工", kind="human",
                                     department="dev_dept"))
        inbox = HumanInbox(store)
        inbox.create(task_id="T-1", title="配合 AI 核查数据", assignee="emp-陈工",
                     due_date="2026-08-30")
        cls.server = DashboardServer(store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_index_200(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.status, 200)
            body = r.read().decode()
            self.assertIn("laoban", body)

    def test_api_tasks(self):
        with urllib.request.urlopen(self.base + "/api/tasks") as r:
            data = json.loads(r.read())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["state"], "doing")

    def test_api_employees(self):
        with urllib.request.urlopen(self.base + "/api/employees") as r:
            data = json.loads(r.read())
            names = {e["name"] for e in data}
            self.assertIn("阿码", names)
            kinds = {e["id"]: e["kind"] for e in data}
            self.assertEqual(kinds["emp-陈工"], "human")

    def test_api_human_tasks_today(self):
        who = urllib.parse.quote("emp-陈工")
        url = f"{self.base}/api/human-tasks?who={who}&date=2026-08-30"
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["title"], "配合 AI 核查数据")
            self.assertEqual(data[0]["source"], "ai_delegated")


if __name__ == "__main__":
    unittest.main()
