"""看板视图权限（RBAC-lite）测试：admin / manager / staff 三级。

组织结构：
  dev_dept ：mgr-dev（负责人，reports_to 指向他）、dev（AI）、emp-chen、emp-xiaoli
  fin_dept ：fin（AI）、emp-wang
  admin    ：boss（permissions.role=admin，无部门）

断言矩阵：
- 花名册：admin 全公司全字段；manager 全公司（跨部门脱敏）；
  staff 仅本部门（他人脱敏，自己全字段）
- 组织架构：staff 看不到 fin_dept 分组
- 任务：仅与本部门 flow_log actor 相关（emp-wang 的任务 staff 看不到）
- 消息/回传结果：仅本人或 admin（manager 不可看下属）
- 队列/待办：本人、admin、本部门 manager
- 未登录（鉴权启用）：GET 数据一律 401
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request

from laoban.core.auth import AuthStore
from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task, TRIAGE
from laoban.core.state_machine import advance
from laoban.dashboard.server import DashboardServer


def _mk_store():
    st = JsonStore(tempfile.mkdtemp())
    st.save_employee(Employee(
        id="mgr-dev", name="沈负责人", kind="human", department="dev_dept",
        title="研发负责人"))
    st.save_employee(Employee(
        id="dev", name="阿码", kind="ai", department="dev_dept",
        model_config={"provider": "mock"}))
    st.save_employee(Employee(
        id="emp-chen", name="陈工", kind="human", department="dev_dept",
        reports_to="mgr-dev"))
    st.save_employee(Employee(
        id="emp-xiaoli", name="小李", kind="human", department="dev_dept",
        reports_to="mgr-dev"))
    st.save_employee(Employee(
        id="fin", name="小金", kind="ai", department="fin_dept"))
    st.save_employee(Employee(
        id="emp-wang", name="王姐", kind="human", department="fin_dept"))
    boss = Employee(id="boss", name="老板", kind="human")
    boss.permissions["role"] = "admin"
    st.save_employee(boss)
    # 任务：dev 流水线 + fin 流水线 + 无 actor 的 pending
    t1 = Task(id="T-DEV-1", title="清洗函数")
    advance(t1, TRIAGE, actor="mgr-dev")
    st.save_task(t1)
    t2 = Task(id="T-FIN-1", title="对账")
    advance(t2, TRIAGE, actor="fin")
    st.save_task(t2)
    st.save_task(Task(id="T-NEW", title="无主流水线"))
    return st


class _Client:
    """带会话 Cookie 的最小 HTTP 客户端。"""

    def __init__(self, base):
        self.base = base
        self.cookie = ""

    def _req(self, method, path, payload=None):
        headers = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                setc = r.headers.get("Set-Cookie", "")
                if "laoban_session=" in setc and "Max-Age=0" not in setc:
                    self.cookie = setc.split(";")[0]
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        return self._req("GET", path)

    def login(self, emp_id, pw):
        return self._req("POST", "/api/login", {"id": emp_id, "password": pw})


class TestRbac(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        cls.auth = AuthStore(cls.store.root)
        cls.auth.set_password("boss", "pw-boss")
        cls.auth.set_password("mgr-dev", "pw-mgr")
        cls.auth.set_password("emp-chen", "pw-chen")
        cls.server = DashboardServer(cls.store, port=0, auth=cls.auth)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _login(self, emp_id, pw):
        c = _Client(self.base)
        c.login(emp_id, pw)
        return c

    # ---- 未登录 ----
    def test_anonymous_get_denied(self):
        anon = _Client(self.base)
        for path in ("/api/tasks", "/api/employees", "/api/org"):
            status, body = anon.get(path)
            self.assertEqual(status, 401, path)

    # ---- 花名册 ----
    def test_admin_sees_all_full(self):
        c = self._login("boss", "pw-boss")
        status, es = c.get("/api/employees")
        self.assertEqual(status, 200)
        self.assertEqual(len(es), 7)
        dev = [e for e in es if e["id"] == "dev"][0]
        self.assertIn("permissions", dev)   # 全字段

    def test_manager_sees_all_but_cross_dept_masked(self):
        c = self._login("mgr-dev", "pw-mgr")
        status, es = c.get("/api/employees")
        self.assertEqual(status, 200)
        self.assertEqual(len(es), 7)
        same = [e for e in es if e["id"] == "dev"][0]
        self.assertIn("permissions", same)          # 本部门全字段
        cross = [e for e in es if e["id"] == "emp-wang"][0]
        self.assertNotIn("permissions", cross)      # 跨部门脱敏
        self.assertNotIn("memory", cross)

    def test_staff_sees_only_own_dept(self):
        c = self._login("emp-chen", "pw-chen")
        status, es = c.get("/api/employees")
        self.assertEqual(status, 200)
        ids = {e["id"] for e in es}
        self.assertEqual(ids, {"mgr-dev", "dev", "emp-chen", "emp-xiaoli"})
        me = [e for e in es if e["id"] == "emp-chen"][0]
        self.assertIn("permissions", me)            # 自己全字段
        peer = [e for e in es if e["id"] == "dev"][0]
        self.assertNotIn("permissions", peer)       # 同事项脱敏

    # ---- 组织架构 ----
    def test_staff_org_excludes_other_dept(self):
        c = self._login("emp-chen", "pw-chen")
        _, org = c.get("/api/org")
        self.assertEqual({d["id"] for d in org}, {"dev_dept"})

    def test_manager_org_includes_all_depts(self):
        c = self._login("mgr-dev", "pw-mgr")
        _, org = c.get("/api/org")
        self.assertEqual({d["id"] for d in org},
                         {"dev_dept", "fin_dept", "（未分配）"})   # 含无部门的 boss

    # ---- 任务 ----
    def test_task_visibility_by_dept_actor(self):
        c = self._login("emp-chen", "pw-chen")
        _, ts = c.get("/api/tasks")
        self.assertEqual({t["id"] for t in ts}, {"T-DEV-1"})   # 无 actor 的不可见

        m = self._login("mgr-dev", "pw-mgr")
        _, ts = m.get("/api/tasks")
        self.assertEqual({t["id"] for t in ts}, {"T-DEV-1"})

        a = self._login("boss", "pw-boss")
        _, ts = a.get("/api/tasks")
        self.assertEqual({t["id"] for t in ts},
                         {"T-DEV-1", "T-FIN-1", "T-NEW"})

    # ---- 消息：仅本人或 admin ----
    def test_messages_only_self_or_admin(self):
        c = self._login("emp-chen", "pw-chen")
        status, _ = c.get("/api/messages?who=emp-xiaoli")
        self.assertEqual(status, 403)
        status, _ = c.get("/api/messages?who=emp-chen")
        self.assertEqual(status, 200)

        m = self._login("mgr-dev", "pw-mgr")
        status, _ = m.get("/api/messages?who=emp-chen")   # manager 也不可看下属
        self.assertEqual(status, 403)

        a = self._login("boss", "pw-boss")
        status, _ = a.get("/api/messages?who=emp-chen")
        self.assertEqual(status, 200)

    # ---- 队列/待办：本人、admin、本部门 manager ----
    def test_queue_dept_scoped(self):
        c = self._login("emp-chen", "pw-chen")
        self.assertEqual(c.get("/api/queue?who=dev")[0], 403)
        self.assertEqual(c.get("/api/queue?who=emp-chen")[0], 200)

        m = self._login("mgr-dev", "pw-mgr")
        self.assertEqual(m.get("/api/queue?who=dev")[0], 200)          # 本部门
        self.assertEqual(m.get("/api/queue?who=fin")[0], 403)          # 跨部门

        a = self._login("boss", "pw-boss")
        self.assertEqual(a.get("/api/queue?who=dev")[0], 200)

    def test_human_tasks_dept_scoped(self):
        c = self._login("emp-chen", "pw-chen")
        self.assertEqual(c.get("/api/human-tasks?who=emp-xiaoli")[0], 403)
        m = self._login("mgr-dev", "pw-mgr")
        self.assertEqual(m.get("/api/human-tasks?who=emp-xiaoli")[0], 200)

    def test_human_results_only_self_or_admin(self):
        c = self._login("emp-chen", "pw-chen")
        self.assertEqual(c.get("/api/human-results?who=emp-xiaoli")[0], 403)
        m = self._login("mgr-dev", "pw-mgr")
        self.assertEqual(m.get("/api/human-results?who=emp-chen")[0], 403)
        a = self._login("boss", "pw-boss")
        self.assertEqual(a.get("/api/human-results?who=emp-chen")[0], 200)

    # ---- /api/me 带角色 ----
    def test_me_returns_role(self):
        c = self._login("emp-chen", "pw-chen")
        _, me = c.get("/api/me")
        self.assertEqual(me["role"], "staff")
        m = self._login("mgr-dev", "pw-mgr")
        _, me = m.get("/api/me")
        self.assertEqual(me["role"], "manager")   # reports_to 自动升级
        a = self._login("boss", "pw-boss")
        _, me = a.get("/api/me")
        self.assertEqual(me["role"], "admin")


class TestRoleOf(unittest.TestCase):
    """角色判定：显式 role 优先；reports_to 有人指向 → manager；默认 staff。"""

    def test_explicit_admin_wins(self):
        st = _mk_store()
        from laoban.dashboard import rbac
        emp = st.load_employee("boss")
        self.assertEqual(rbac.role_of(st, emp), "admin")   # 即便有人向他汇报

    def test_reports_to_promotes_manager(self):
        st = _mk_store()
        from laoban.dashboard import rbac
        self.assertEqual(rbac.role_of(st, st.load_employee("mgr-dev")), "manager")

    def test_default_staff(self):
        st = _mk_store()
        from laoban.dashboard import rbac
        self.assertEqual(rbac.role_of(st, st.load_employee("emp-wang")), "staff")


if __name__ == "__main__":
    unittest.main()
