import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request

from laoban.dashboard.server import DashboardServer
from laoban.core.store import JsonStore
from laoban.core.task import Task, ASSIGNED, DOING
from laoban.core.employee import Employee
from laoban.core.messenger import send as msg_send
from laoban.core.workstation import enqueue


class TestDashboardOrgViews(unittest.TestCase):
    """看板 v0.2 新增 API：org（部门分组）/ messages / queue。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        store = JsonStore(cls.root)
        # 组织：dev_dept（AI+人类混合）、ops_dept
        store.save_task(Task(id="T-1", title="写函数", state=DOING))
        store.save_task(Task(id="T-2", title="补测试", state=ASSIGNED))
        store.save_employee(Employee(id="dev", name="阿码", title="开发工程师",
                                     department="dev_dept"))
        store.save_employee(Employee(id="emp-chen", name="陈工", kind="human",
                                     title="数据核查员", department="dev_dept"))
        store.save_employee(Employee(id="pm", name="老谋", title="项目经理",
                                     department="ops_dept"))
        # 消息 + 工位队列
        msg_send(store, "pm", "dev", "请优先处理 T-1", task_id="T-1")
        enqueue(store, "dev", "T-1")
        enqueue(store, "dev", "T-2")
        cls.server = DashboardServer(store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _get(self, path: str):
        with urllib.request.urlopen(self.base + path) as r:
            self.assertEqual(r.status, 200)
            return json.loads(r.read())

    def test_api_org_groups_by_department(self):
        data = self._get("/api/org")
        depts = {d["id"] for d in data}
        self.assertEqual(depts, {"dev_dept", "ops_dept"})
        by_id = {d["id"]: d for d in data}
        # 人机混合部门：AI 与人类同组
        dev_ids = {e["id"] for e in by_id["dev_dept"]["employees"]}
        self.assertEqual(dev_ids, {"dev", "emp-chen"})
        kinds = {e["id"]: e["kind"] for e in by_id["dev_dept"]["employees"]}
        self.assertEqual(kinds["dev"], "ai")
        self.assertEqual(kinds["emp-chen"], "human")
        # 队列数带出
        dev_emp = [e for e in by_id["dev_dept"]["employees"] if e["id"] == "dev"][0]
        self.assertEqual(dev_emp["queue"], ["T-1", "T-2"])

    def test_api_org_employee_fields(self):
        data = self._get("/api/org")
        all_emps = [e for d in data for e in d["employees"]]
        dev = [e for e in all_emps if e["id"] == "dev"][0]
        self.assertEqual(dev["status"], "active")
        self.assertEqual(dev["title"], "开发工程师")

    def test_api_messages_inbox_and_sent(self):
        who = urllib.parse.quote("dev")
        data = self._get(f"/api/messages?who={who}")
        self.assertEqual(len(data["inbox"]), 1)
        self.assertEqual(data["inbox"][0]["from"], "pm")
        self.assertEqual(data["inbox"][0]["content"], "请优先处理 T-1")
        self.assertEqual(data["sent"], [])
        # 发件人视角
        who_pm = urllib.parse.quote("pm")
        data_pm = self._get(f"/api/messages?who={who_pm}")
        self.assertEqual(len(data_pm["sent"]), 1)
        self.assertEqual(data_pm["sent"][0]["to"], "dev")

    def test_api_messages_requires_who(self):
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/api/messages")

    def test_api_queue(self):
        who = urllib.parse.quote("dev")
        data = self._get(f"/api/queue?who={who}")
        self.assertEqual(len(data), 2)
        titles = {q["title"] for q in data}
        self.assertEqual(titles, {"写函数", "补测试"})
        states = {q["state"] for q in data}
        self.assertIn("doing", states)

    def test_api_queue_unknown_who(self):
        who = urllib.parse.quote("ghost")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + f"/api/queue?who={who}")

    def test_html_contains_new_views(self):
        with urllib.request.urlopen(self.base + "/") as r:
            body = r.read().decode()
        self.assertIn("组织架构", body)
        self.assertIn("点对点消息", body)
        self.assertIn("工位任务队列", body)


if __name__ == "__main__":
    unittest.main()
