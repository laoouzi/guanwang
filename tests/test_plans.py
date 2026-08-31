"""个人任务计划视图测试：周期分组 + 来源区分 + 完成情况。

覆盖：
- Task 字段：plan_horizon（day/…/year）/ created_by / assignee 落盘回读；
- 派单链路写 assignee（持久：验收出队后仍可追溯）；
- 来源判定：created_by == assignee → 个人计划；否则被动分配；无 assignee → 未指派；
- /api/plans：按周期分组（含未分类）、每组完成数/完成率/按时/超时、overall 汇总；
- 提交端点：plan_horizon 透传 + 非法值 400；
- RBAC：admin 任何人/全公司；manager 本部门；staff 强制本人（查别人也被拦回自己）。
"""
from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone

from laoban.core.employee import Employee
from laoban.core.task import Task, HORIZONS, HORIZON_LABELS
from laoban.core.store import JsonStore
from laoban.core.state_machine import advance
from laoban.core.workstation import assign_task_auto
from laoban.dashboard.server import _task_source
from tests.test_rbac import _mk_store, _Client


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _mk_task(tid, title="任务", horizon="", created_by="", assignee="",
             state="pending", due_at=""):
    t = Task(id=tid, title=title, plan_horizon=horizon,
             created_by=created_by, assignee=assignee, due_at=due_at)
    t.state = state
    return t


class TestTaskFields(unittest.TestCase):
    """plan_horizon / created_by / assignee 序列化。"""

    def test_roundtrip(self):
        t = _mk_task("T1", horizon="week", created_by="boss", assignee="dev")
        d = t.to_dict()
        self.assertEqual(d["plan_horizon"], "week")
        self.assertEqual(d["created_by"], "boss")
        self.assertEqual(d["assignee"], "dev")
        t2 = Task.from_dict(d)
        self.assertEqual(t2.plan_horizon, "week")
        self.assertEqual(t2.created_by, "boss")
        self.assertEqual(t2.assignee, "dev")

    def test_defaults_empty(self):
        t = Task.from_dict({"id": "T", "title": "旧数据"})
        self.assertEqual(t.plan_horizon, "")
        self.assertEqual(t.created_by, "")
        self.assertEqual(t.assignee, "")

    def test_horizon_constants(self):
        self.assertEqual(len(HORIZONS), 6)
        self.assertEqual(HORIZON_LABELS["quarter"], "季度计划")
        self.assertEqual(HORIZON_LABELS["half_year"], "半年计划")
        self.assertEqual(HORIZON_LABELS[""], "未分类")


class TestAssigneePersisted(unittest.TestCase):
    """派单写 assignee：验收出队后个人计划视图仍能按人追溯。"""

    def setUp(self):
        self.store = JsonStore("/tmp/laoban-test-plans-store") if False else _mk_store()
        # _mk_store 每次新建目录；直接用其 store
        self.store = self.store if isinstance(self.store, JsonStore) else self.store

    def test_assign_writes_assignee(self):
        st = _mk_store()
        t = _mk_task("T-p1", horizon="day")
        st.save_task(t)
        assign_task_auto(st, "T-p1", "dev")
        task = st.load_task("T-p1")
        self.assertEqual(task.assignee, "dev")
        # 完成出队（模拟验收后 dequeue）
        from laoban.core.workstation import dequeue
        dequeue(st, "dev", "T-p1")
        task.state = "done"
        st.save_task(task)
        # 出队后 assignee 仍在（不靠队列扫描）
        self.assertEqual(st.load_task("T-p1").assignee, "dev")


class TestTaskSource(unittest.TestCase):

    def test_self_plan(self):
        t = _mk_task("T", created_by="chen", assignee="chen")
        self.assertEqual(_task_source(t), "self")

    def test_passive_assigned(self):
        t = _mk_task("T", created_by="boss", assignee="chen")
        self.assertEqual(_task_source(t), "assigned")

    def test_unassigned(self):
        t = _mk_task("T", created_by="boss")
        self.assertEqual(_task_source(t), "unassigned")
        # 旧数据（无 created_by）有 assignee → 也算被动
        t2 = _mk_task("T2", assignee="chen")
        self.assertEqual(_task_source(t2), "assigned")


class TestPlansEndpoint(unittest.TestCase):
    """免鉴权（admin 视角）：周期分组 + 完成情况 + who 过滤。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        now = datetime.now(timezone.utc)
        # chen 个人计划：日计划 1 完成（按时）+ 1 进行中
        t1 = _mk_task("T-c1", "晨会纪要", "day", "emp-chen", "emp-chen",
                      "done", due_at=_iso(now + timedelta(hours=1)))
        t2 = _mk_task("T-c2", "整理客户反馈", "day", "emp-chen", "emp-chen",
                      "assigned")
        # chen 被动分配：周计划，完成但超时
        t3 = _mk_task("T-c3", "周报", "week", "boss", "emp-chen",
                      "done", due_at=_iso(now - timedelta(hours=1)))
        # dev（AI）被动：月计划，完成按时
        t4 = _mk_task("T-d1", "接口联调", "month", "boss", "dev",
                      "done", due_at=_iso(now + timedelta(days=1)))
        # 未指派：季度计划
        t5 = _mk_task("T-x1", "招聘规划", "quarter", "boss")
        # 完成时间戳：done 任务 updated_at = now
        for t in (t1, t3, t4):
            t.updated_at = _iso(now)
        for t in (t1, t2, t3, t4, t5):
            cls.store.save_task(t)
        cls.server = _mk_server(cls.store)
        cls.client = _Client(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _get(self, qs=""):
        status, body = self.client.get(f"/api/plans{qs}")
        return status, body

    def test_grouping_by_horizon(self):
        status, plans = self._get()
        self.assertEqual(status, 200)
        # _mk_store 预置 3 个无周期任务 → 另有「未分类」组
        labels = {h["label"] for h in plans["horizons"]}
        self.assertEqual(labels, {"日计划", "周计划", "月计划", "季度计划", "未分类"})
        for h in plans["horizons"]:
            if h["label"] == "日计划":
                self.assertEqual(h["total"], 2)
                self.assertEqual(h["done"], 1)
                self.assertAlmostEqual(h["completion_rate"], 0.5)
                self.assertEqual(h["on_time"], 1)
            if h["label"] == "周计划":
                self.assertEqual(h["done"], 1)
                self.assertEqual(h["late"], 1)
            if h["label"] == "未分类":
                self.assertEqual(h["total"], 3)   # _mk_store 预置

    def test_overall_summary(self):
        _, plans = self._get()
        o = plans["overall"]
        # 新建 5 + 预置 3 = 8；完成 3 + 预置 0 = 3
        self.assertEqual(o["total"], 8)
        self.assertEqual(o["done"], 3)
        self.assertEqual(o["on_time"], 2)   # T-c1 按时 + T-d1 按时
        self.assertEqual(o["late"], 1)      # T-c3 超时

    def test_source_labels(self):
        _, plans = self._get()
        day = next(h for h in plans["horizons"] if h["label"] == "日计划")
        sources = {t["source"] for t in day["tasks"]}
        self.assertEqual(sources, {"self"})
        week = next(h for h in plans["horizons"] if h["label"] == "周计划")
        self.assertEqual(week["tasks"][0]["source"], "assigned")
        quarter = next(h for h in plans["horizons"] if h["label"] == "季度计划")
        self.assertEqual(quarter["tasks"][0]["source"], "unassigned")

    def test_who_filter(self):
        status, plans = self._get("?who=emp-chen")
        self.assertEqual(status, 200)
        self.assertEqual(plans["who"], "emp-chen")
        o = plans["overall"]
        self.assertEqual(o["total"], 3)   # chen 的 3 项（不含 dev 的月计划）
        self.assertEqual(o["done"], 2)

    def test_task_on_time_flags(self):
        _, plans = self._get("?who=emp-chen")
        day = next(h for h in plans["horizons"] if h["label"] == "日计划")
        by_id = {t["id"]: t for t in day["tasks"]}
        self.assertIs(by_id["T-c1"]["on_time"], True)   # 完成且按时
        self.assertIsNone(by_id["T-c2"]["on_time"])     # 未完成 → —
        week = next(h for h in plans["horizons"] if h["label"] == "周计划")
        self.assertIs(week["tasks"][0]["on_time"], False)   # 完成但超时


class TestPlansRBAC(unittest.TestCase):
    """鉴权模式：admin 任何人 / manager 本部门 / staff 强制本人。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        now = datetime.now(timezone.utc)
        # chen（staff，dev_dept）：个人日计划
        cls.store.save_task(_mk_task("T-c1", "chen 日计划", "day",
                                     "emp-chen", "emp-chen", "assigned"))
        # wang（mkt_dept，非 chen 部门）：个人日计划
        cls.store.save_task(_mk_task("T-w1", "wang 日计划", "day",
                                     "emp-wang", "emp-wang", "assigned"))
        # dev_dept 的 AI：月计划（manager 应可见）
        cls.store.save_task(_mk_task("T-d1", "dev 月计划", "month",
                                     "boss", "dev", "done",
                                     due_at=_iso(now + timedelta(days=1))))
        from laoban.core.auth import AuthStore
        au = AuthStore(cls.store.root)
        au.set_password("boss", "pw-boss")
        au.set_password("mgr-dev", "pw-mgr")
        au.set_password("emp-chen", "pw-chen")
        cls.server = _mk_server(cls.store, auth=au)
        base = f"http://127.0.0.1:{cls.server.port}"
        cls.admin = _Client(base)
        cls.manager = _Client(base)
        cls.staff = _Client(base)
        for c, (i, p) in ((cls.admin, ("boss", "pw-boss")),
                          (cls.manager, ("mgr-dev", "pw-mgr")),
                          (cls.staff, ("emp-chen", "pw-chen"))):
            status, _ = c.post("/api/login", {"id": i, "password": p})
            assert status == 200

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_admin_default_sees_company_wide(self):
        status, plans = self.admin.get("/api/plans")
        self.assertEqual(status, 200)
        # 新建 3 + _mk_store 预置 3 = 6
        self.assertEqual(plans["overall"]["total"], 6)
        self.assertEqual(plans["scope"], "全公司")

    def test_admin_can_query_anyone(self):
        status, plans = self.admin.get("/api/plans?who=emp-wang")
        self.assertEqual(status, 200)
        self.assertEqual(plans["overall"]["total"], 1)

    def test_manager_default_dept_scope(self):
        status, plans = self.manager.get("/api/plans")
        self.assertEqual(status, 200)
        # dev_dept：chen + dev（mgr-dev 自己）→ 不含 mkt 的 wang
        titles = {t["title"] for h in plans["horizons"] for t in h["tasks"]}
        self.assertIn("chen 日计划", titles)
        self.assertIn("dev 月计划", titles)
        self.assertNotIn("wang 日计划", titles)

    def test_manager_cannot_query_other_dept(self):
        status, body = self.manager.get("/api/plans?who=emp-wang")
        self.assertEqual(status, 403)

    def test_manager_can_query_own_member(self):
        status, plans = self.manager.get("/api/plans?who=emp-chen")
        self.assertEqual(status, 200)
        self.assertEqual(plans["overall"]["total"], 1)

    def test_staff_forced_to_self_even_querying_other(self):
        # staff 查别人 → 强制回自己（不报错、不越权）
        status, plans = self.staff.get("/api/plans?who=emp-wang")
        self.assertEqual(status, 200)
        titles = {t["title"] for h in plans["horizons"] for t in h["tasks"]}
        self.assertEqual(titles, {"chen 日计划"})
        self.assertEqual(plans["who"], "emp-chen")

    def test_unauthenticated_rejected(self):
        c = _Client(f"http://127.0.0.1:{self.server.port}")
        status, _ = c.get("/api/plans")
        self.assertEqual(status, 401)


class TestSubmitHorizon(unittest.TestCase):
    """提交端点：plan_horizon 透传 + 校验。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_store()
        cls.server = _mk_server(cls.store)
        cls.client = _Client(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_submit_with_horizon(self):
        status, body = self.client.post("/api/task/submit",
                                        {"title": "季度目标",
                                         "plan_horizon": "quarter"})
        self.assertEqual(status, 200)
        self.assertEqual(body["plan_horizon"], "quarter")
        t = self.store.load_task(body["id"])
        self.assertEqual(t.plan_horizon, "quarter")

    def test_submit_invalid_horizon(self):
        status, body = self.client.post("/api/task/submit",
                                        {"title": "坏周期",
                                         "plan_horizon": "century"})
        self.assertEqual(status, 400)
        self.assertIn("plan_horizon", body["error"])

    def test_submit_without_horizon_ok(self):
        status, body = self.client.post("/api/task/submit", {"title": "无周期"})
        self.assertEqual(status, 200)
        self.assertEqual(body["plan_horizon"], "")


def _mk_server(store, auth=None):
    from laoban.dashboard.server import DashboardServer
    server = DashboardServer(store, port=0, auth=auth)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


if __name__ == "__main__":
    unittest.main()
