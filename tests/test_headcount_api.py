"""编制申请看板化测试：提交 / 决策 / RBAC / 入职闭环。

覆盖：
- manager 提交 → pending；列表仅见自己提交的；
- staff 提交 → 403（无部门管理权）；
- admin 通过 → HR 自动入职（hired_emp_id 落档、花名册 +1）；
- clone_ai：通过后生成源员工分身（source=cloned）；
- admin 驳回 → rejected + 理由落档；重复决策 → 409；
- 缺 reason / 非法 hire_type → 400。
"""
from __future__ import annotations

import threading
import unittest

from laoban.core.auth import AuthStore
from laoban.core.store import JsonStore
from laoban.core.employee import Employee
from laoban.dashboard.server import DashboardServer
from tests.test_rbac import _mk_store, _Client


def _server_with_auth():
    st = _mk_store()
    au = AuthStore(st.root)
    au.set_password("boss", "pw-boss")
    au.set_password("mgr-dev", "pw-mgr")
    au.set_password("emp-chen", "pw-chen")
    server = DashboardServer(st, port=0, auth=au)
    return st, server


def _login(client: _Client, emp_id: str, pw: str):
    status, _ = client.post("/api/login", {"id": emp_id, "password": pw})
    assert status == 200, f"登录失败：{emp_id}"


class TestHeadcountFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.store, cls.server = _server_with_auth()
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                       daemon=True)
        cls.thread.start()
        base = f"http://127.0.0.1:{cls.server.port}"
        cls.mgr = _Client(base)
        cls.admin = _Client(base)
        cls.staff = _Client(base)
        _login(cls.mgr, "mgr-dev", "pw-mgr")
        _login(cls.admin, "boss", "pw-boss")
        _login(cls.staff, "emp-chen", "pw-chen")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_full_flow_submit_approve_hire(self):
        st = self.store
        n0 = len(st.list_employees())
        # 1. manager 提交（命中岗位模板用 org.json，这里走无模板路径）
        status, body = self.mgr.post("/api/headcount/submit", {
            "reason": "任务积压，需要加人", "hire_type": "new_ai",
            "role": "dev2", "department": "dev_dept", "headcount": 1})
        self.assertEqual(status, 200)
        rid = body["id"]
        self.assertEqual(body["status"], "pending")

        # 2. manager 列表只看到自己的申请
        status, rows = self.mgr.get("/api/headcount")
        self.assertEqual(status, 200)
        self.assertTrue(all(r["requester"] == "mgr-dev" for r in rows))
        self.assertTrue(any(r["id"] == rid for r in rows))

        # 3. admin 通过 → 自动入职
        status, body = self.admin.post("/api/headcount/decide",
                                       {"id": rid, "approved": True})
        self.assertEqual(status, 200)
        hired = body["hired_emp_id"]
        emp = st.load_employee(hired)
        self.assertIsNotNone(emp)
        self.assertEqual(emp.department, "dev_dept")
        self.assertEqual(len(st.list_employees()), n0 + 1)

        # 4. 重复决策 → 409
        status, _ = self.admin.post("/api/headcount/decide",
                                    {"id": rid, "approved": False})
        self.assertEqual(status, 409)

    def test_clone_ai_hire(self):
        st = self.store
        status, body = self.mgr.post("/api/headcount/submit", {
            "reason": "复用成熟员工能力", "hire_type": "clone_ai",
            "source_emp_id": "dev", "department": "dev_dept"})
        self.assertEqual(status, 200)
        status, body = self.admin.post("/api/headcount/decide",
                                       {"id": body["id"], "approved": True})
        self.assertEqual(status, 200)
        emp = st.load_employee(body["hired_emp_id"])
        self.assertEqual(emp.source, "cloned")
        self.assertIn("分身", emp.name)

    def test_reject_with_reason(self):
        status, body = self.mgr.post("/api/headcount/submit", {
            "reason": "临时加人", "hire_type": "hire_human"})
        self.assertEqual(status, 200)
        rid = body["id"]
        status, body = self.admin.post("/api/headcount/decide",
                                       {"id": rid, "approved": False,
                                        "reason": "预算不足"})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "rejected")
        # 理由落档
        from laoban.recruitment import get_request
        req = get_request(self.store, rid)
        self.assertEqual(req["reject_reason"], "预算不足")
        # 花名册没变
        self.assertFalse(req.get("hired_emp_id"))

    def test_staff_cannot_submit(self):
        status, body = self.staff.post("/api/headcount/submit", {
            "reason": "我想加个帮手", "hire_type": "new_ai"})
        self.assertEqual(status, 403)

    def test_manager_cannot_decide(self):
        status, _ = self.mgr.post("/api/headcount/decide",
                                  {"id": "HR-xxx", "approved": True})
        self.assertEqual(status, 403)

    def test_validation(self):
        status, _ = self.mgr.post("/api/headcount/submit", {
            "reason": "", "hire_type": "new_ai"})
        self.assertEqual(status, 400)
        status, _ = self.mgr.post("/api/headcount/submit", {
            "reason": "x", "hire_type": "magic"})
        self.assertEqual(status, 400)
        # clone 不给源 → 后端校验 409
        status, _ = self.mgr.post("/api/headcount/submit", {
            "reason": "x", "hire_type": "clone_ai"})
        self.assertEqual(status, 409)


class TestHeadcountFreeAuth(unittest.TestCase):
    """免鉴权模式：dashboard 视角 = admin，可直接提交+决策。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        st = JsonStore(tempfile.mkdtemp())
        st.save_employee(Employee(id="dev", name="阿码", kind="ai",
                                  department="dev_dept"))
        cls.store = st
        cls.server = DashboardServer(st, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                       daemon=True)
        cls.thread.start()
        cls.client = _Client(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_admin_view_all(self):
        status, body = self.client.post("/api/headcount/submit", {
            "reason": "演示", "hire_type": "new_ai"})
        self.assertEqual(status, 200)
        status, rows = self.client.get("/api/headcount")
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)   # 免鉴权 = admin 全量可见


if __name__ == "__main__":
    unittest.main()
