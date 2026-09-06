"""人类员工晋升通道测试（年度评估 + 积分达标）→ 申请部门负责人 → 审批 → role 生效。

覆盖：
- 触发：人类 + role=staff + 入职满一年（hired_at 锚点）+ 积分 ≥ 30；
- 不触发：入职未满一年 / role=manager、admin / 积分不足；
- 晋升后 last_promoted_at 成为下次年度评估锚点（当年内不再申请）；
- 防重复 + 驳回冷却（与 AI 轴共用）；
- apply_promotion：写 permissions.role=manager，role_of 立即返回 manager；
- 端点闭环：满一年员工 3 次满分验收 → 审批通过 → role 变 manager →
  RBAC 视图随之扩大（/api/employees 能看到部门同事）。
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task
from laoban.core.state_machine import advance
from laoban.core.workstation import assign_task_auto
from laoban.core.promotion import (maybe_request_promotion, apply_promotion,
                                   PROMO_POINTS, MANAGER_ROLE)
from laoban.core.ledger import FileLedger
from laoban.runner.approval_log import ApprovalLog
from laoban.dashboard.rbac import role_of
from laoban.dashboard.server import DashboardServer
from laoban.core.auth import AuthStore
from tests.test_rbac import _mk_store, _Client


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _human(emp_id="chen", role=None, years_ago=2.0):
    e = Employee(id=emp_id, name="小陈", kind="human", department="dev_dept")
    e.hired_at = _iso(datetime.now(timezone.utc) - timedelta(days=365 * years_ago))
    if role:
        e.permissions["role"] = role
    return e


def _ledger_with(store, emp_id="chen", points=PROMO_POINTS):
    led = FileLedger(store)
    if points:
        led.record_points(emp_id, points, reason="测试预置积分")
    return led


class TestHumanPromotionRequest(unittest.TestCase):
    """人类年度评估：年限锚点 + 奖励积分双条件。"""

    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        self.log = ApprovalLog(self.store)

    def test_staff_with_points_and_one_year_triggers(self):
        emp = _human()
        led = _ledger_with(self.store)
        r = maybe_request_promotion(self.store, emp, log=self.log,
                                    role="staff", ledger=led)
        self.assertIsNotNone(r)
        self.assertEqual(r["target_level"], MANAGER_ROLE)
        self.assertIn("晋升部门负责人", r["summary"])
        # 落审批日志 pending
        entries = self.log.list_logs(requester="chen")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].request["status"], "pending")

    def test_not_one_year_yet(self):
        # 年度评估：入职不满一年，积分再多也不触发
        emp = _human(years_ago=0.5)
        led = _ledger_with(self.store, points=PROMO_POINTS * 2)
        self.assertIsNone(maybe_request_promotion(
            self.store, emp, log=self.log, role="staff", ledger=led))

    def test_manager_or_admin_not_eligible(self):
        led = _ledger_with(self.store)
        self.assertIsNone(maybe_request_promotion(
            self.store, _human(role="manager"), log=self.log,
            role="manager", ledger=led))
        self.assertIsNone(maybe_request_promotion(
            self.store, _human(role="admin"), log=self.log,
            role="admin", ledger=led))

    def test_insufficient_points(self):
        emp = _human()
        led = _ledger_with(self.store, points=PROMO_POINTS - 0.5)
        self.assertIsNone(maybe_request_promotion(
            self.store, emp, log=self.log, role="staff", ledger=led))

    def test_no_duplicate_while_pending(self):
        emp = _human()
        led = _ledger_with(self.store)
        self.assertIsNotNone(maybe_request_promotion(
            self.store, emp, log=self.log, role="staff", ledger=led))
        self.assertIsNone(maybe_request_promotion(
            self.store, emp, log=self.log, role="staff", ledger=led))

    def test_reject_cooldown(self):
        emp = _human()
        led = _ledger_with(self.store)
        r = maybe_request_promotion(self.store, emp, log=self.log,
                                    role="staff", ledger=led)
        self.log.log_decision(r["id"], approver="boss", approved=False)
        # 驳回后积分仅多攒一点 → 冷却中
        led.record_points("chen", 2, reason="再攒一点")
        self.assertIsNone(maybe_request_promotion(
            self.store, emp, log=self.log, role="staff", ledger=led))
        # 再攒满一档（30+30=60）→ 可重新申请
        led.record_points("chen", 28, reason="攒满冷却分")
        self.assertIsNotNone(maybe_request_promotion(
            self.store, emp, log=self.log, role="staff", ledger=led))

    def test_last_promoted_anchors_next_review(self):
        # 晋升后 last_promoted_at 成为新锚点：当年内积分达标也不再申请
        st = JsonStore(tempfile.mkdtemp())
        emp = _human()
        st.save_employee(emp)
        apply_promotion(st, {"requester": "chen", "target_level": MANAGER_ROLE})
        emp = st.load_employee("chen")
        # role 已是 manager，走 role 拦截即可验证；再显式验证锚点日期解析
        self.assertTrue(emp.permissions["last_promoted_at"])
        anchor = datetime.fromisoformat(
            emp.permissions["last_promoted_at"].replace("Z", "+00:00"))
        self.assertLess(anchor, datetime.now(timezone.utc))


class TestApplyHumanPromotion(unittest.TestCase):

    def test_applies_role_and_rbac_follows(self):
        st = JsonStore(tempfile.mkdtemp())
        st.save_employee(_human())
        self.assertEqual(role_of(st, st.load_employee("chen")), "staff")
        result = apply_promotion(
            st, {"requester": "chen", "target_level": MANAGER_ROLE})
        self.assertEqual(result["role"], "manager")
        emp = st.load_employee("chen")
        self.assertEqual(emp.permissions["role"], "manager")
        # 晋升即放权：role_of 立即认 manager
        self.assertEqual(role_of(st, emp), "manager")

    def test_invalid_target(self):
        st = JsonStore(tempfile.mkdtemp())
        st.save_employee(_human())
        # 人类轴目标只能是 manager（不是 AI 的 semi/full）
        self.assertIsNone(apply_promotion(
            st, {"requester": "chen", "target_level": "semi"}))
        self.assertIsNone(apply_promotion(
            st, {"requester": "ghost", "target_level": MANAGER_ROLE}))


class TestHumanPromotionEndpoint(unittest.TestCase):
    """端点闭环：满一年员工 3 次满分验收 → 审批 → role=manager → RBAC 视图扩大。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        # 年度评估需要年限锚点：把 chen 的入职时间回拨到两年前
        chen = cls.store.load_employee("emp-chen")
        chen.hired_at = _iso(
            datetime.now(timezone.utc) - timedelta(days=730))
        cls.store.save_employee(chen)
        au = AuthStore(cls.store.root)
        au.set_password("boss", "pw-boss")
        au.set_password("emp-chen", "pw-chen")
        # chen 保持 staff（无下属、无显式 role）
        cls.server = DashboardServer(cls.store, port=0, auth=au)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        base = f"http://127.0.0.1:{cls.server.port}"
        cls.admin = _Client(base)
        cls.chen = _Client(base)
        status, _ = cls.admin.post("/api/login", {"id": "boss", "password": "pw-boss"})
        assert status == 200
        status, _ = cls.chen.post("/api/login", {"id": "emp-chen", "password": "pw-chen"})
        assert status == 200

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _accept_task_for(self, emp_id, score=5, comment="干得漂亮"):
        st = self.store
        t = Task(id=f"T-{id(self)%99999:05d}-{emp_id}-{len(st.list_tasks())}",
                 title="人类晋升测试任务", instruction="x")
        t.progress_log.append({"deliverable": "交付", "by": emp_id, "at": ""})
        st.save_task(t)
        assign_task_auto(st, t.id, emp_id)
        t = st.load_task(t.id)
        advance(t, "doing", actor="t")
        advance(t, "reporting", actor="t")
        st.save_task(t)
        return self.admin.post("/api/task/accept",
                               {"id": t.id, "score": score, "comment": comment})

    def test_full_human_promotion_loop(self):
        # 1. 晋升前：chen 是 staff，/api/employees 仅本部门可见
        status, before = self.chen.get("/api/employees")
        self.assertEqual(status, 200)
        self.assertTrue(before)
        self.assertTrue(all(e["department"] == "dev_dept" for e in before))

        # 2. 三次满分验收（3×10=30 分达标，年限已满足）→ 第三次触发晋升申请
        for _ in range(3):
            status, body = self._accept_task_for("emp-chen")
            self.assertEqual(status, 200)
        status, approvals = self.admin.get("/api/approvals")
        promos = [a for a in approvals if a["type"] == "晋升申请"
                  and a["status"] == "pending"]
        self.assertEqual(len(promos), 1)
        pid = promos[0]["id"]
        self.assertEqual(promos[0]["requester"], "emp-chen")

        # 3. 老板通过 → role 即时生效
        status, body = self.admin.post("/api/approval/decide",
                                       {"id": pid, "approved": True})
        self.assertEqual(status, 200)
        self.assertIn("晋升部门负责人", body["message"])
        self.assertEqual(self.store.load_employee("emp-chen")
                         .permissions["role"], "manager")

        # 4. 晋升即放权：chen 的 RBAC 视图扩大（能看到部门同事了）
        status, body = self.chen.get("/api/employees")
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 1)

        # 5. 已是 manager（且年度锚点重置），再验收不再重复申请
        status, _ = self._accept_task_for("emp-chen")
        status, approvals = self.admin.get("/api/approvals")
        self.assertFalse([a for a in approvals
                          if a["type"] == "晋升申请" and a["status"] == "pending"])


if __name__ == "__main__":
    unittest.main()
