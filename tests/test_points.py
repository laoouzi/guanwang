"""奖励积分机制测试：统一记分（人类/AI）+ 成本核算 + ROI 榜。

覆盖：
- 记分规则：points_for_acceptance 按评分线性折算（满分 10，0.5 步进）；
- 成本核算：AI 按 token 单价、人类按时薪折算（未配置为 0，不虚造）；
- 三榜输出：ai/human 分榜按积分降序 + roi 统一榜（cost=0 不入榜，
  terminated 员工剔除）；
- /api/points：RBAC 可见范围（admin 全量 / manager 本部门 / staff 本人）；
- 验收记账闭环：满分 +10、低分（≤2）驳回 -5。
"""
from __future__ import annotations

import tempfile
import threading
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task
from laoban.core.state_machine import advance
from laoban.core.workstation import assign_task_auto
from laoban.core.points import (points_for_acceptance, accept_cost,
                                leaderboard, PENALTY_REJECTION)
from laoban.core.ledger import FileLedger
from laoban.dashboard.server import DashboardServer
from laoban.core.auth import AuthStore
from tests.test_rbac import _mk_store, _Client


class TestPointsRules(unittest.TestCase):

    def test_linear_scaling(self):
        self.assertEqual(points_for_acceptance(5), 10.0)
        self.assertEqual(points_for_acceptance(4), 8.0)
        self.assertEqual(points_for_acceptance(3), 6.0)
        self.assertEqual(points_for_acceptance(1), 2.0)

    def test_half_step_rounding(self):
        self.assertEqual(points_for_acceptance(2), 4.0)

    def test_score_clamped(self):
        self.assertEqual(points_for_acceptance(0), 0.0)
        self.assertEqual(points_for_acceptance(99), 10.0)
        self.assertEqual(points_for_acceptance(-3), 0.0)


class TestAcceptCost(unittest.TestCase):

    def test_ai_cost_by_tokens(self):
        emp = Employee(id="dev", name="阿码", kind="ai")
        emp.compensation["cost_per_1k_tokens"] = 0.02
        self.assertAlmostEqual(accept_cost(emp, usage_tokens=1500), 0.03)

    def test_ai_without_rate_is_zero(self):
        self.assertEqual(accept_cost(Employee(id="dev", name="a", kind="ai"),
                                     usage_tokens=1500), 0.0)

    def test_human_cost_by_hourly(self):
        emp = Employee(id="chen", name="陈", kind="human")
        emp.compensation["salary_monthly"] = 8800.0   # 时薪 = 8800/22/8 = 50
        self.assertAlmostEqual(accept_cost(emp, elapsed_sec=3600), 50.0)
        self.assertAlmostEqual(accept_cost(emp, elapsed_sec=1800), 25.0)

    def test_human_without_salary_or_time_is_zero(self):
        emp = Employee(id="chen", name="陈", kind="human")
        self.assertEqual(accept_cost(emp, elapsed_sec=3600), 0.0)
        emp.compensation["salary_monthly"] = 8800.0
        self.assertEqual(accept_cost(emp, elapsed_sec=0), 0.0)


class TestLeaderboard(unittest.TestCase):

    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        ai = Employee(id="dev", name="阿码", kind="ai", department="dev_dept")
        ai.compensation["cost_per_1k_tokens"] = 0.02
        hu = Employee(id="chen", name="陈工", kind="human", department="dev_dept")
        hu.compensation["salary_monthly"] = 8800.0
        gone = Employee(id="gone", name="离职者", kind="ai", department="dev_dept")
        gone.status = "terminated"
        no_cost = Employee(id="free", name="免费员工", kind="ai", department="dev_dept")
        for e in (ai, hu, gone, no_cost):
            self.store.save_employee(e)
        led = FileLedger(self.store)
        led.record_points("dev", 40, reason="4 次满分")
        led.record_completion("dev", task_id="T1", cost=2.0, elapsed=10)
        led.record_points("chen", 20, reason="2 次满分")
        led.record_completion("chen", task_id="T2", cost=100.0, elapsed=7200)
        led.record_points("gone", 99, reason="离职前的辉煌")
        led.record_points("free", 5, reason="无成本员工")
        self.board = leaderboard(self.store, led)

    def test_split_boards_by_kind(self):
        self.assertEqual([r["id"] for r in self.board["ai"]], ["dev", "free"])
        self.assertEqual([r["id"] for r in self.board["human"]], ["chen"])
        # terminated 不上榜
        self.assertNotIn("gone", [r["id"] for r in self.board["ai"]])

    def test_points_desc_order(self):
        self.assertEqual(self.board["ai"][0]["points"], 40.0)
        self.assertEqual(self.board["ai"][1]["points"], 5.0)

    def test_roi_only_with_cost(self):
        # free（cost=0）与 gone（terminated）不进 ROI 榜
        ids = [r["id"] for r in self.board["roi"]]
        self.assertIn("dev", ids)
        self.assertIn("chen", ids)
        self.assertNotIn("free", ids)
        self.assertNotIn("gone", ids)

    def test_roi_value_and_order(self):
        # dev: 40/2 = 20 分/元；chen: 20/100 = 0.2 分/元 → dev 排前
        self.assertEqual(self.board["roi"][0]["id"], "dev")
        self.assertEqual(self.board["roi"][0]["roi"], 20.0)
        self.assertEqual(self.board["roi"][1]["id"], "chen")
        self.assertEqual(self.board["roi"][1]["roi"], 0.2)

    def test_rules_exposed(self):
        self.assertIn("points_per_task", self.board["rules"])
        self.assertIn("note", self.board["rules"])


class TestPointsEndpoint(unittest.TestCase):
    """/api/points 可见范围：admin 全量 / manager 本部门 / staff 仅本人。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        # 预置积分账本（服务器构造时从 ledger.json 加载）
        led = FileLedger(cls.store)
        led.record_points("dev", 40, reason="dev 攒分")
        led.record_completion("dev", task_id="T1", cost=2.0, elapsed=10)
        led.record_points("fin", 30, reason="fin 攒分")
        led.record_completion("fin", task_id="T2", cost=6.0, elapsed=10)
        led.record_points("emp-chen", 15, reason="chen 攒分")
        led.record_points("emp-wang", 12, reason="wang 攒分")
        au = AuthStore(cls.store.root)
        au.set_password("boss", "pw-boss")
        au.set_password("mgr-dev", "pw-mgr")
        au.set_password("emp-chen", "pw-chen")
        cls.server = DashboardServer(cls.store, port=0, auth=au)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        base = f"http://127.0.0.1:{cls.server.port}"
        cls.admin = _Client(base)
        cls.manager = _Client(base)
        cls.staff = _Client(base)
        for client, cred in ((cls.admin, ("boss", "pw-boss")),
                             (cls.manager, ("mgr-dev", "pw-mgr")),
                             (cls.staff, ("emp-chen", "pw-chen"))):
            status, _ = client.post("/api/login",
                                    {"id": cred[0], "password": cred[1]})
            assert status == 200

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _ids(self, board, key):
        return {r["id"] for r in board[key]}

    def test_admin_sees_all(self):
        status, board = self.admin.get("/api/points")
        self.assertEqual(status, 200)
        self.assertEqual(self._ids(board, "ai"), {"dev", "fin"})
        self.assertEqual(self._ids(board, "human"),
                         {"boss", "mgr-dev", "emp-chen", "emp-xiaoli",
                          "emp-wang"})
        self.assertEqual(self._ids(board, "roi"), {"dev", "fin"})
        self.assertIn("rules", board)
        # 财务速览：可见范围（全公司）总积分 / 总成本 / 整体 ROI
        s = board["summary"]
        self.assertEqual(s["scope"], "全公司")
        self.assertAlmostEqual(s["total_points"], 97.0)   # 40+30+15+12
        self.assertAlmostEqual(s["total_cost"], 8.0)      # 2.0+6.0
        self.assertAlmostEqual(s["roi"], 12.12)

    def test_manager_scoped_to_department(self):
        status, board = self.manager.get("/api/points")
        self.assertEqual(status, 200)
        # dev_dept 成员 + 自己：dev/emp-chen/emp-xiaoli/mgr-dev，不含 fin/emp-wang
        self.assertEqual(self._ids(board, "ai"), {"dev"})
        self.assertEqual(self._ids(board, "human"),
                         {"mgr-dev", "emp-chen", "emp-xiaoli"})
        self.assertFalse(self._ids(board, "roi") & {"fin"})

    def test_staff_sees_only_self(self):
        status, board = self.staff.get("/api/points")
        self.assertEqual(status, 200)
        # 仅本人：AI 榜空（chen 是人类）、人类榜只有自己、ROI 榜空（无成本记录）
        self.assertEqual(self._ids(board, "ai"), set())
        self.assertEqual(self._ids(board, "human"), {"emp-chen"})
        self.assertEqual(self._ids(board, "roi"), set())
        # 财务速览随可见范围收权：只剩本人 15 分，无成本 → roi=None
        s = board["summary"]
        self.assertAlmostEqual(s["total_points"], 15.0)
        self.assertAlmostEqual(s["total_cost"], 0.0)
        self.assertIsNone(s["roi"])


class TestAcceptPointsFlow(unittest.TestCase):
    """验收端到端记分：满分 +10 入账；低分（≤2）驳回 -5。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        cls.server = DashboardServer(cls.store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _Client(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _accepted_task(self, emp_id, score, cost=0.0):
        st = self.store
        t = Task(id=f"T-{id(self)%99999:05d}-{emp_id}-{score}-{len(st.list_tasks())}",
                 title="积分测试任务", instruction="x")
        t.progress_log.append({"deliverable": "交付", "by": emp_id, "at": "",
                               "cost": cost, "elapsed": 10.0})
        st.save_task(t)
        assign_task_auto(st, t.id, emp_id)
        t = st.load_task(t.id)
        advance(t, "doing", actor="t")
        advance(t, "reporting", actor="t")
        st.save_task(t)
        return self.client.post("/api/task/accept",
                                {"id": t.id, "score": score, "comment": "评语"})

    def test_full_score_awards_and_ledger_records_cost(self):
        status, body = self._accepted_task("dev", 5, cost=0.05)
        self.assertEqual(status, 200)
        self.assertAlmostEqual(body["points"], 10.0)
        # 成本落账（绩效面板可见）
        led = FileLedger(self.store)
        self.assertAlmostEqual(led.stats("dev")["total_cost"], 0.05)

    def test_low_score_penalizes(self):
        # fin 未有历史积分：满分 +10 → 低分 -5 → 累计 5
        self._accepted_task("fin", 5)
        status, body = self._accepted_task("fin", 1)
        self.assertEqual(status, 200)
        self.assertAlmostEqual(body["points"], 5.0)
        led = FileLedger(self.store)
        self.assertEqual(led.stats("fin")["rejection_count"], 1)
        # 积分流水最后一条是驳回扣分
        log = led.points_log("fin")
        self.assertAlmostEqual(log[-1]["delta"], -PENALTY_REJECTION)


if __name__ == "__main__":
    unittest.main()
