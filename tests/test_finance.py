"""CFO 财务周报测试：周期工具 / 报告生成 / 归档幂等 / 环比 / 老板通知 / API。

覆盖：
- 周工具：week_key（ISO 周）、week_bounds（周一 ~ 周日）、prev_week_key（跨年）；
- generate_cost_report：公司级汇总（积分/成本/ROI）、部门分组（成员按积分降序）、
  terminated 剔除、无产出者不上榜、归档落盘、同周覆盖；
- maybe_generate_weekly_report：首次生成并通知老板（消息落盘），
  同周重复调用返回 None（幂等）；
- 环比：上周数据归档后，本周报告含 compare（delta/pct/period）；
- /api/finance：admin 可见 current+history；staff 403（财务数据仅老板）。
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.finance import (week_key, week_bounds, prev_week_key,
                                 generate_cost_report,
                                 maybe_generate_weekly_report,
                                 load_reports, ARCHIVE_NAME)
from laoban.core.ledger import FileLedger
from laoban.core.auth import AuthStore
from laoban.dashboard.server import DashboardServer
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM
from laoban.runner.worker import WorkerLoop
from tests.test_rbac import _mk_store, _Client


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class TestWeekUtils(unittest.TestCase):

    def test_week_key_iso(self):
        # 2026-08-31 是周一 → W36
        self.assertEqual(week_key(datetime(2026, 8, 31, tzinfo=timezone.utc)),
                         "2026-W36")
        # 周日属于同一周
        self.assertEqual(week_key(datetime(2026, 9, 6, tzinfo=timezone.utc)),
                         "2026-W36")

    def test_week_bounds_monday_to_sunday(self):
        start, end = week_bounds(datetime(2026, 9, 2, 15, 30, tzinfo=timezone.utc))
        self.assertEqual(start, datetime(2026, 8, 31, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 6, 23, 59, 59, tzinfo=timezone.utc))

    def test_prev_week_key(self):
        self.assertEqual(prev_week_key("2026-W36"), "2026-W35")
        # 跨年：W01 的上一周是上一年的 W52（2025 非 53 周年）
        self.assertEqual(prev_week_key("2026-W01"), "2025-W52")
        # 锚点周日不回退自身：W36 锚 1月4日（周日）也必须回到 W35
        self.assertNotEqual(prev_week_key("2026-W36"), "2026-W36")

    def test_prev_prev_chain_returns_same_key(self):
        # 对任意周，本周一 -1 天所在周 = 上一周（回退 7 天不变）
        self.assertEqual(prev_week_key(prev_week_key("2026-W36")), "2026-W34")


class _FinanceCase:
    """共用脚手架：员工 + 本周/上周账目。"""

    def _setup(self):
        self.store = JsonStore(tempfile.mkdtemp())
        for e in (
            Employee(id="dev", name="阿码", kind="ai", department="dev_dept"),
            Employee(id="emp-chen", name="陈工", kind="human",
                     department="dev_dept"),
            Employee(id="fin", name="小金", kind="ai", department="fin_dept"),
            Employee(id="gone", name="离职者", kind="ai", department="dev_dept"),
            Employee(id="idle", name="闲人", kind="ai", department="dev_dept"),
        ):
            if e.id == "gone":
                e.status = "terminated"
            self.store.save_employee(e)
        self.now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)   # W36 周三
        start, _ = week_bounds(self.now)
        self.week_start = start

    def _ledger_with(self, at: datetime) -> FileLedger:
        led = FileLedger(self.store)
        ts = _iso(at)
        # dev：18 分 / 3.6 元（ROI 5）；chen：8 分 / 80 元（ROI 0.1）
        led.record_points("dev", 18, reason="验收", kind="acceptance", at=ts)
        led.record_completion("dev", task_id="T1", cost=3.6, elapsed=9,
                              score=5, on_time=True, at=ts)
        led.record_points("emp-chen", 8, reason="验收", kind="acceptance", at=ts)
        led.record_completion("emp-chen", task_id="T2", cost=80.0, elapsed=3600,
                              score=4, on_time=False, at=ts)
        # 离职者有账也不统计
        led.record_points("gone", 99, reason="离职前", kind="acceptance", at=ts)
        return led


class TestGenerateReport(unittest.TestCase, _FinanceCase):

    def setUp(self):
        self._setup()

    def test_company_summary(self):
        led = self._ledger_with(self.week_start + timedelta(days=1))
        r = generate_cost_report(self.store, led, now=self.now)
        c = r["company"]
        self.assertEqual(c["points"], 26.0)          # 18 + 8（gone 不计）
        self.assertAlmostEqual(c["cost"], 83.6)
        self.assertAlmostEqual(c["roi"], 0.31)
        self.assertEqual(c["completion_count"], 2)
        # 无上周归档 → 无环比
        self.assertIsNone(r.get("compare"))

    def test_departments_grouped_and_sorted(self):
        led = self._ledger_with(self.week_start + timedelta(days=1))
        r = generate_cost_report(self.store, led, now=self.now)
        depts = {d["department"]: d for d in r["departments"]}
        # fin / idle 本周无产出 → 不出现
        self.assertEqual(set(depts), {"dev_dept"})
        members = depts["dev_dept"]["members"]
        self.assertEqual([m["id"] for m in members], ["dev", "emp-chen"])
        self.assertAlmostEqual(depts["dev_dept"]["points"], 26.0)
        self.assertAlmostEqual(depts["dev_dept"]["cost"], 83.6)

    def test_archive_written_and_same_week_overwrites(self):
        led = self._ledger_with(self.week_start + timedelta(days=1))
        r1 = generate_cost_report(self.store, led, now=self.now)
        self.assertTrue((self.store.root / ARCHIVE_NAME).exists())
        # 同周再生成 → 覆盖（归档仍 1 份），且周期 key 一致
        led.record_points("dev", 10, reason="追加", kind="acceptance",
                          at=_iso(self.now))
        r2 = generate_cost_report(self.store, led, now=self.now)
        self.assertEqual(len(load_reports(self.store)), 1)
        self.assertEqual(r2["period"]["key"], r1["period"]["key"])
        self.assertEqual(r2["company"]["points"], 36.0)

    def test_out_of_period_entries_excluded(self):
        # 上周记账不算本周：公司级为空 → departments 空、advice 兜底
        led = self._ledger_with(self.week_start - timedelta(days=7))
        r = generate_cost_report(self.store, led, now=self.now)
        self.assertEqual(r["company"]["points"], 0.0)
        self.assertEqual(r["departments"], [])

    def test_budget_advice_template_fallback(self):
        # 无 gateway → 模板建议（最高/最低 ROI 部门）
        led = self._ledger_with(self.week_start + timedelta(days=1))
        r = generate_cost_report(self.store, led, now=self.now)
        self.assertIn("dev_dept", r["budget_advice"])
        self.assertIn("建议", r["budget_advice"])


class TestWeeklyReportIdempotent(unittest.TestCase, _FinanceCase):

    def setUp(self):
        self._setup()
        self.ledger = self._ledger_with(self.week_start + timedelta(days=1))
        # 老板（admin）在场才能收到通知
        boss = Employee(id="boss", name="老板", kind="human")
        boss.permissions["role"] = "admin"
        self.store.save_employee(boss)

    def test_first_generates_then_skips(self):
        r = maybe_generate_weekly_report(self.store, self.ledger, now=self.now)
        self.assertIsNotNone(r)
        self.assertEqual(r["period"]["key"], "2026-W36")
        # 同周重复 → None（幂等）
        self.assertIsNone(
            maybe_generate_weekly_report(self.store, self.ledger, now=self.now))
        # 归档只有 1 份
        self.assertEqual(len(load_reports(self.store)), 1)

    def test_boss_notified(self):
        maybe_generate_weekly_report(self.store, self.ledger, now=self.now)
        msgs = self.store.root / "messages"
        files = list(msgs.glob("*.json")) if msgs.exists() else []
        contents = [f.read_text(encoding="utf-8") for f in files]
        self.assertTrue(any('"to": "boss"' in c and '"from": "fin"' in c
                            and "CFO 周报" in c for c in contents),
                        f"老板未收到周报通知：{contents}")


class TestWeekOverWeekCompare(unittest.TestCase, _FinanceCase):

    def setUp(self):
        self._setup()

    def test_compare_with_prev_week(self):
        # 上周（W35）先归档：26 分 - 8（核减）= 18 分 / 83.6 元
        led_prev = self._ledger_with(self.week_start - timedelta(days=7))
        led_prev.record_points("dev", -8, reason="核减", kind="rejection",
                               at=_iso(self.week_start - timedelta(days=7)))
        generate_cost_report(self.store, led_prev,
                             now=self.week_start - timedelta(days=7))
        # 本周（W36）：26 分 / 83.6 元
        led = self._ledger_with(self.week_start + timedelta(days=1))
        r = generate_cost_report(self.store, led, now=self.now)
        cmp = r["compare"]
        self.assertEqual(cmp["period"], "2026-W35")
        self.assertEqual(cmp["points"]["previous"], 18.0)
        self.assertEqual(cmp["points"]["current"], 26.0)
        self.assertEqual(cmp["points"]["delta"], 8.0)
        self.assertAlmostEqual(cmp["points"]["pct"], 44.4, places=1)
        # 成本环比：同口径 83.6 → delta 0、pct 0（基数 >0 必有 pct）
        self.assertEqual(cmp["cost"]["delta"], 0.0)
        self.assertIn("pct", cmp["cost"])
        self.assertEqual(cmp["cost"]["pct"], 0.0)


class TestWorkerLoopWeeklyTrigger(unittest.TestCase):
    """WorkerLoop 集成：每周首次 tick 触发周报；同周后续 tick 幂等跳过。"""

    def test_tick_triggers_report_once_per_week(self):
        st = _mk_store()          # test_worker 同款：dev（AI）/ fin（AI）等
        gw = LLMGateway()
        gw.register_provider("dev", MockLLM(responses=["交付内容。"]))
        gw.register_provider("fin", MockLLM(responses=["财务建议。"]))
        loop = WorkerLoop(st, gw)
        # 第一轮 tick：周报生成并归档
        loop.tick()
        reports = load_reports(st)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["period"]["key"],
                         week_key(datetime.now(timezone.utc)))
        # 第二轮 tick（同周）：不再生成（归档不增）
        loop.tick()
        self.assertEqual(len(load_reports(st)), 1)
        # 周报无产出（本周无验收账目）→ 公司级为空、建议走模板兜底
        self.assertEqual(reports[0]["company"]["points"], 0.0)
        self.assertTrue(reports[0]["budget_advice"])


class TestFinanceEndpoint(unittest.TestCase):
    """/api/finance：admin 可见；staff 403（财务数据仅老板）。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        led = FileLedger(cls.store)
        now = datetime.now(timezone.utc)
        led.record_points("dev", 18, reason="验收", kind="acceptance")
        led.record_completion("dev", task_id="T1", cost=3.6, elapsed=9,
                              score=5, on_time=True)
        generate_cost_report(cls.store, led, now=now)
        au = AuthStore(cls.store.root)
        au.set_password("boss", "pw-boss")
        au.set_password("emp-chen", "pw-chen")
        cls.server = DashboardServer(cls.store, port=0, auth=au)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        base = f"http://127.0.0.1:{cls.server.port}"
        cls.admin = _Client(base)
        cls.staff = _Client(base)
        for client, cred in ((cls.admin, ("boss", "pw-boss")),
                             (cls.staff, ("emp-chen", "pw-chen"))):
            status, _ = client.post("/api/login",
                                    {"id": cred[0], "password": cred[1]})
            assert status == 200

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_admin_sees_current_and_history(self):
        status, fin = self.admin.get("/api/finance")
        self.assertEqual(status, 200)
        self.assertIsNotNone(fin["current"])
        self.assertEqual(len(fin["history"]), 1)
        self.assertEqual(fin["current"]["company"]["points"], 18.0)
        self.assertIn("departments", fin["current"])
        self.assertIn("budget_advice", fin["current"])

    def test_staff_forbidden(self):
        status, body = self.staff.get("/api/finance")
        self.assertEqual(status, 403)
        self.assertIn("老板", body["error"])


if __name__ == "__main__":
    unittest.main()
