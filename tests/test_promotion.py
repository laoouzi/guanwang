"""员工晋升通道测试：连续验收通过 → 自动申请 → 老板审批 → 自主等级生效。

覆盖：
- 触发条件：AI + 最近 3 条经验全 success + 当前等级可升；
- 不触发：人类员工 / 经验不足 / 近期有 failure / 已 full；
- 防重复：pending 不重复提；
- 驳回冷却：需再攒 3 条新 success 才可重新申请；
- apply_promotion：写回 autonomy_level；
- 端点闭环：3 次高分验收 → 审批队列出现晋升申请 → admin 通过 → dev 升 semi。
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
                                   PROMO_STREAK)
from laoban.runner.approval_log import ApprovalLog
from laoban.dashboard.server import DashboardServer
from tests.test_rbac import _mk_store, _Client


def _emp(kind="ai", level=None, exps=None):
    e = Employee(id="dev", name="阿码", kind=kind, department="dev_dept",
                 model_config={"provider": "p"})
    if level:
        e.permissions["autonomy_level"] = level
    e.memory["experiences"] = exps or []
    return e


def _succ(n):
    return [{"outcome": "success", "learned": f"经验{i}"} for i in range(n)]


class TestMaybeRequest(unittest.TestCase):

    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        self.log = ApprovalLog(self.store)

    def test_triggers_on_streak(self):
        emp = _emp(exps=_succ(PROMO_STREAK))
        r = maybe_request_promotion(self.store, emp, log=self.log)
        self.assertIsNotNone(r)
        self.assertEqual(r["target_level"], "semi")
        # 已落审批日志且 pending
        entries = self.log.list_logs(requester="dev")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].request["status"], "pending")
        self.assertEqual(entries[0].request["type"], "晋升申请")

    def test_no_duplicate_while_pending(self):
        emp = _emp(exps=_succ(PROMO_STREAK))
        self.assertIsNotNone(maybe_request_promotion(self.store, emp, log=self.log))
        self.assertIsNone(maybe_request_promotion(self.store, emp, log=self.log))

    def test_human_never(self):
        emp = _emp(kind="human", exps=_succ(5))
        self.assertIsNone(maybe_request_promotion(self.store, emp, log=self.log))

    def test_insufficient_experiences(self):
        emp = _emp(exps=_succ(PROMO_STREAK - 1))
        self.assertIsNone(maybe_request_promotion(self.store, emp, log=self.log))

    def test_failure_breaks_streak(self):
        exps = _succ(PROMO_STREAK)
        exps[-1]["outcome"] = "failure"
        emp = _emp(exps=exps)
        self.assertIsNone(maybe_request_promotion(self.store, emp, log=self.log))

    def test_full_level_cap(self):
        emp = _emp(level="full", exps=_succ(10))
        self.assertIsNone(maybe_request_promotion(self.store, emp, log=self.log))

    def test_reject_cooldown(self):
        emp = _emp(exps=_succ(PROMO_STREAK))
        r = maybe_request_promotion(self.store, emp, log=self.log)
        self.log.log_decision(r["id"], approver="boss", approved=False)
        # 补 2 条仍不够（需 3+3=6 条）
        emp.memory["experiences"].extend(_succ(2))
        self.assertIsNone(maybe_request_promotion(self.store, emp, log=self.log))
        # 补满第 3 条 → 可重新申请
        emp.memory["experiences"].extend(_succ(1))
        r2 = maybe_request_promotion(self.store, emp, log=self.log)
        self.assertIsNotNone(r2)
        self.assertEqual(r2["target_level"], "semi")

    def test_semi_promotes_to_full(self):
        emp = _emp(level="semi", exps=_succ(PROMO_STREAK))
        r = maybe_request_promotion(self.store, emp, log=self.log)
        self.assertEqual(r["target_level"], "full")


class TestApplyPromotion(unittest.TestCase):

    def test_applies_level(self):
        st = JsonStore(tempfile.mkdtemp())
        st.save_employee(_emp())
        result = apply_promotion(st, {"requester": "dev", "target_level": "semi"})
        self.assertEqual(result["autonomy_level"], "semi")
        self.assertEqual(st.load_employee("dev").permissions["autonomy_level"], "semi")

    def test_unknown_employee_or_level(self):
        st = JsonStore(tempfile.mkdtemp())
        self.assertIsNone(apply_promotion(st, {"requester": "ghost", "target_level": "semi"}))
        st.save_employee(_emp())
        self.assertIsNone(apply_promotion(st, {"requester": "dev", "target_level": "boss"}))


class TestPromotionEndpoint(unittest.TestCase):
    """免鉴权端点闭环：3 次高分验收 → 晋升申请 → 通过 → 生效。"""

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
        # 1. 三次高分验收（评语非空 → success 经验）
        for _ in range(3):
            status, body = self._accept_new_task(5)
            self.assertEqual(status, 200)
        # 第三次触发晋升申请
        status, body = self._last = self.client.get("/api/approvals")
        promos = [a for a in body if a["type"] == "晋升申请"]
        self.assertEqual(len(promos), 1)
        self.assertEqual(promos[0]["requester"], "dev")
        pid = promos[0]["id"]
        self.assertEqual(promos[0]["status"], "pending")

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

        # 4. 再来 3 次高分 → 可申请 full
        for _ in range(3):
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
