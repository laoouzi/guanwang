"""AI 员工晋升通道测试（积分驱动）：验收攒分 → 达标自动申请 → 老板审批 → 等级生效。

覆盖：
- 触发条件：AI + 奖励积分 ≥ PROMO_POINTS + 当前等级可升；
- 不触发：积分不足 / 已 full；
- 防重复：pending 不重复提；
- 驳回冷却：需再攒满一档晋升积分（points_mark + PROMO_POINTS）；
- apply_promotion：写回 autonomy_level；
- 端点闭环：3 次满分验收（3×10=30 分）→ 审批队列出现晋升申请 →
  admin 通过 → dev 升 semi → 继续攒分升 full → 封顶不再申请。
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
from laoban.core.promotion import (maybe_request_promotion, apply_promotion,
                                   PROMO_POINTS)
from laoban.core.ledger import FileLedger
from laoban.runner.approval_log import ApprovalLog
from laoban.dashboard.server import DashboardServer
from tests.test_rbac import _mk_store, _Client


def _emp(kind="ai", level=None):
    e = Employee(id="dev", name="阿码", kind=kind, department="dev_dept",
                 model_config={"provider": "p"})
    if level:
        e.permissions["autonomy_level"] = level
    return e


def _ledger_with(store, emp_id="dev", points=PROMO_POINTS):
    led = FileLedger(store)
    if points:
        led.record_points(emp_id, points, reason="测试预置积分")
    return led


class TestMaybeRequest(unittest.TestCase):
    """积分驱动的 AI 晋升申请（ledger 供分）。"""

    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        self.log = ApprovalLog(self.store)

    def test_triggers_on_points(self):
        emp = _emp()
        led = _ledger_with(self.store, points=PROMO_POINTS)
        r = maybe_request_promotion(self.store, emp, log=self.log, ledger=led)
        self.assertIsNotNone(r)
        self.assertEqual(r["target_level"], "semi")
        self.assertEqual(r["points"], PROMO_POINTS)
        # 已落审批日志且 pending
        entries = self.log.list_logs(requester="dev")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].request["status"], "pending")
        self.assertEqual(entries[0].request["type"], "晋升申请")

    def test_insufficient_points(self):
        emp = _emp()
        led = _ledger_with(self.store, points=PROMO_POINTS - 0.5)
        self.assertIsNone(
            maybe_request_promotion(self.store, emp, log=self.log, ledger=led))

    def test_no_ledger_no_promotion(self):
        # 没有账本（无积分数据）不触发，避免误判
        self.assertIsNone(
            maybe_request_promotion(self.store, _emp(), log=self.log))

    def test_no_duplicate_while_pending(self):
        emp = _emp()
        led = _ledger_with(self.store)
        self.assertIsNotNone(
            maybe_request_promotion(self.store, emp, log=self.log, ledger=led))
        self.assertIsNone(
            maybe_request_promotion(self.store, emp, log=self.log, ledger=led))

    def test_full_level_cap(self):
        emp = _emp(level="full")
        led = _ledger_with(self.store, points=PROMO_POINTS * 2)
        self.assertIsNone(
            maybe_request_promotion(self.store, emp, log=self.log, ledger=led))

    def test_reject_cooldown(self):
        emp = _emp()
        led = _ledger_with(self.store)
        r = maybe_request_promotion(self.store, emp, log=self.log, ledger=led)
        self.log.log_decision(r["id"], approver="boss", approved=False)
        # 驳回后：积分仅多攒一点（仍 < 30+30）→ 冷却中
        led.record_points("dev", 2, reason="再攒一点")
        self.assertIsNone(
            maybe_request_promotion(self.store, emp, log=self.log, ledger=led))
        # 再攒满一档（30+30=60）→ 可重新申请
        led.record_points("dev", 28, reason="攒满冷却分")
        r2 = maybe_request_promotion(self.store, emp, log=self.log, ledger=led)
        self.assertIsNotNone(r2)
        self.assertEqual(r2["target_level"], "semi")

    def test_semi_promotes_to_full(self):
        emp = _emp(level="semi")
        led = _ledger_with(self.store)
        r = maybe_request_promotion(self.store, emp, log=self.log, ledger=led)
        self.assertEqual(r["target_level"], "full")


class TestApplyPromotion(unittest.TestCase):

    def test_applies_level(self):
        st = JsonStore(tempfile.mkdtemp())
        st.save_employee(_emp())
        result = apply_promotion(st, {"requester": "dev", "target_level": "semi"})
        self.assertEqual(result["autonomy_level"], "semi")
        emp = st.load_employee("dev")
        self.assertEqual(emp.permissions["autonomy_level"], "semi")
        # 晋升时间落档（下次年度评估锚点）
        self.assertTrue(emp.permissions["last_promoted_at"])

    def test_unknown_employee_or_level(self):
        st = JsonStore(tempfile.mkdtemp())
        self.assertIsNone(apply_promotion(st, {"requester": "ghost", "target_level": "semi"}))
        st.save_employee(_emp())
        self.assertIsNone(apply_promotion(st, {"requester": "dev", "target_level": "boss"}))


class TestPromotionEndpoint(unittest.TestCase):
    """免鉴权端点闭环：满分验收攒分 → 晋升申请 → 通过 → 生效 → 升满封顶。"""

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

    def _accept_new_task(self, score, comment="做得好"):
        st = self.store
        t = Task(id=f"T-{id(self)%99999:05d}-{score}-{len(st.list_tasks())}",
                 title="晋升测试任务", instruction="x")
        t.progress_log.append({"deliverable": "交付", "by": "dev", "at": ""})
        st.save_task(t)
        assign_task_auto(st, t.id, "dev")
        t = st.load_task(t.id)
        advance(t, "doing", actor="t")
        advance(t, "reporting", actor="t")
        st.save_task(t)
        return self.client.post("/api/task/accept",
                                {"id": t.id, "score": score, "comment": comment})

    def test_full_promotion_loop(self):
        # 1. 三次满分验收（3×10=30 分达标）→ 第三次触发晋升申请
        for i in range(3):
            status, body = self._accept_new_task(5)
            self.assertEqual(status, 200)
            # 返回的是累计积分：10 → 20 → 30
            self.assertAlmostEqual(body.get("points", 0.0), 10.0 * (i + 1))
        status, body = self.client.get("/api/approvals")
        promos = [a for a in body if a["type"] == "晋升申请"
                  and a["status"] == "pending"]
        self.assertEqual(len(promos), 1)
        self.assertEqual(promos[0]["requester"], "dev")
        pid = promos[0]["id"]

        # 2. 员工等级尚未变（等审批）
        self.assertEqual(self.store.load_employee("dev")
                         .permissions.get("autonomy_level", "supervised"),
                         "supervised")

        # 3. admin 通过 → 立即生效
        status, body = self.client.post("/api/approval/decide",
                                        {"id": pid, "approved": True})
        self.assertEqual(status, 200)
        self.assertIn("自主等级升至 semi", body["message"])
        self.assertEqual(self.store.load_employee("dev")
                         .permissions["autonomy_level"], "semi")

        # 4. 再攒 30 分（累计 60）→ 可申请 full（积分累计即时触发，无需等 N 次）
        status, _ = self._accept_new_task(5)
        self.assertEqual(status, 200)
        status, body = self.client.get("/api/approvals")
        promos = [a for a in body if a["type"] == "晋升申请"
                  and a["status"] == "pending"]
        self.assertEqual(len(promos), 1)
        status, body = self.client.post("/api/approval/decide",
                                        {"id": promos[0]["id"], "approved": True})
        self.assertEqual(self.store.load_employee("dev")
                         .permissions["autonomy_level"], "full")

        # 5. full 封顶，继续验收不再申请
        status, _ = self._accept_new_task(5)
        status, body = self.client.get("/api/approvals")
        self.assertFalse([a for a in body if a["type"] == "晋升申请"
                          and a["status"] == "pending"])


if __name__ == "__main__":
    unittest.main()
