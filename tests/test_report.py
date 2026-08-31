"""部门级复盘报告测试：GET /api/report 聚合 + RBAC 可见范围。

覆盖：
- 聚合：按部门汇总完成数/驳回次数/教训数/自动复盘数；成员明细含最新教训与自主等级；
- RBAC：admin 全部门；manager 仅本部门；staff 仅本人（单行分组）。
"""
from __future__ import annotations

import threading
import unittest

from laoban.core.auth import AuthStore
from laoban.dashboard.server import DashboardServer
from tests.test_rbac import _mk_store, _Client


def _prepare(store):
    """给 dev 制造绩效 + 经验；给 fin 制造经验（须在起 server 前落账）。"""
    from laoban.core.ledger import FileLedger
    ledger = FileLedger(store)
    ledger.record_completion("dev")
    ledger.record_completion("dev")
    ledger.record_completion("dev")
    ledger.record_rejection("dev")
    # 经验：dev = 1 条自动低分教训 + 2 条成功经验；fin = 1 条成功
    dev = store.load_employee("dev")
    dev.memory["experiences"] = [
        {"outcome": "success", "learned": "模板可复用"},
        {"outcome": "failure", "learned": "先写单测再交付", "auto": True},
        {"outcome": "success", "learned": "输出格式对齐"},
    ]
    store.save_employee(dev)
    fin = store.load_employee("fin")
    fin.memory["experiences"] = [{"outcome": "success", "learned": "对账要双人"}]
    store.save_employee(fin)


class TestReport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        au = AuthStore(cls.store.root)
        au.set_password("boss", "pw-boss")
        au.set_password("mgr-dev", "pw-mgr")
        au.set_password("emp-chen", "pw-chen")
        au.set_password("emp-wang", "pw-wang")
        _prepare(cls.store)
        cls.server = DashboardServer(cls.store, port=0, auth=au)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        base = f"http://127.0.0.1:{cls.server.port}"
        cls.admin = _Client(base)
        cls.mgr = _Client(base)
        cls.staff_dev_dept = _Client(base)
        cls.staff_fin = _Client(base)
        for c, i, p in ((cls.admin, "boss", "pw-boss"),
                        (cls.mgr, "mgr-dev", "pw-mgr"),
                        (cls.staff_dev_dept, "emp-chen", "pw-chen"),
                        (cls.staff_fin, "emp-wang", "pw-wang")):
            status, _ = c.post("/api/login", {"id": i, "password": p})
            assert status == 200

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_admin_aggregation(self):
        status, rows = self.admin.get("/api/report")
        self.assertEqual(status, 200)
        by_dept = {r["department"]: r for r in rows}
        self.assertIn("dev_dept", by_dept)
        self.assertIn("fin_dept", by_dept)

        dev = by_dept["dev_dept"]
        self.assertEqual(dev["completion_count"], 3)
        self.assertEqual(dev["rejections"], 1)
        self.assertEqual(dev["lessons"], 1)
        self.assertEqual(dev["auto_reviews"], 1)
        m = {x["id"]: x for x in dev["members"]}
        self.assertEqual(m["dev"]["lessons"], 1)
        self.assertEqual(m["dev"]["wins"], 2)
        self.assertEqual(m["dev"]["latest_lesson"], "先写单测再交付")
        self.assertEqual(m["dev"]["autonomy_level"], "supervised")

        fin = by_dept["fin_dept"]
        self.assertEqual(fin["lessons"], 0)      # fin 的经验是 success
        self.assertEqual(fin["members"][0]["wins"], 1)

    def test_manager_scoped_to_own_dept(self):
        status, rows = self.mgr.get("/api/report")
        self.assertEqual(status, 200)
        self.assertEqual({r["department"] for r in rows}, {"dev_dept"})
        members = {x["id"] for r in rows for x in r["members"]}
        self.assertIn("dev", members)
        self.assertNotIn("fin", members)

    def test_staff_sees_only_self(self):
        status, rows = self.staff_dev_dept.get("/api/report")
        self.assertEqual(status, 200)
        members = [x for r in rows for x in r["members"]]
        self.assertEqual([x["id"] for x in members], ["emp-chen"])
        # 财务部员工看不到研发部
        status, rows = self.staff_fin.get("/api/report")
        members = [x for r in rows for x in r["members"]]
        self.assertEqual([x["id"] for x in members], ["emp-wang"])


class TestReportFreeAuth(unittest.TestCase):
    """免鉴权模式 = admin 视角，全部门可见。"""

    @classmethod
    def setUpClass(cls):
        st = _mk_store()
        _prepare(st)
        cls.server = DashboardServer(st, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _Client(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_all_departments(self):
        status, rows = self.client.get("/api/report")
        self.assertEqual(status, 200)
        self.assertTrue({"dev_dept", "fin_dept"} <= {r["department"] for r in rows})


if __name__ == "__main__":
    unittest.main()
