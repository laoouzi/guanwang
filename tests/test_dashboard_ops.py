"""看板操作端点（老板驾驶舱）测试：提交 / 派单 / 验收 / 审批 / 绩效。

覆盖：
- 免鉴权模式：提交→派单→验收全流程闭环（含状态机、队列、经验回写、账本）
- RBAC：staff 不可派单/审批；manager 只能派/验收本部门；跨部门 403
- 验收：评分 1-5 边界、状态守卫（非 doing/reporting 拒绝）、低分记 failure
- 审批：仅 admin 可决策；重复决策 404；账本记人类介入
- 绩效：三角色可见范围 + FileLedger 重启不丢（新实例读到旧数据）
"""
from __future__ import annotations

import tempfile
import threading
import unittest

from laoban.core.auth import AuthStore
from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING
from laoban.core.state_machine import advance
from laoban.core.workstation import assign_task_auto, enqueue
from laoban.dashboard.server import DashboardServer
from laoban.runner.approval_log import (ApprovalLog, ApprovalRequest)
from tests.test_rbac import _mk_store, _Client


def _mk_free_store():
    """免鉴权模式测试库：dev_dept + fin_dept + boss。"""
    st = JsonStore(tempfile.mkdtemp())
    st.save_employee(Employee(
        id="mgr-dev", name="沈负责人", kind="human", department="dev_dept"))
    st.save_employee(Employee(
        id="dev", name="阿码", kind="ai", department="dev_dept"))
    st.save_employee(Employee(
        id="emp-chen", name="陈工", kind="human", department="dev_dept",
        reports_to="mgr-dev"))
    st.save_employee(Employee(
        id="emp-wang", name="王姐", kind="human", department="fin_dept"))
    return st


def _mk_auth_store():
    st = _mk_store()
    au = AuthStore(st.root)
    au.set_password("boss", "pw-boss")
    au.set_password("mgr-dev", "pw-mgr")
    au.set_password("emp-chen", "pw-chen")
    return st, au


class TestOpsFreeAuth(unittest.TestCase):
    """免鉴权模式：全流程闭环（向后兼容，未设口令= admin 视角）。"""

    @classmethod
    def setUpClass(cls):
        cls.store = _mk_free_store()
        cls.server = DashboardServer(cls.store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _Client(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_full_loop_submit_assign_accept(self):
        # 0. 账本基线（增量断言，避免依赖测试执行顺序）
        _, perf0 = self.client.get("/api/perf")
        base = perf0.get("dev", {}).get("completion_count", 0)

        # 1. 提交
        status, body = self.client.post("/api/task/submit",
                                        {"title": "清洗函数", "instruction": "写代码"})
        self.assertEqual(status, 200)
        tid = body["id"]
        self.assertEqual(body["state"], "pending")

        # 2. 派单（免鉴权 = admin）
        status, body = self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "assigned")
        # 队列入列
        from laoban.core.workstation import queue_of
        self.assertIn(tid, queue_of(self.store, "dev"))

        # 3. 推进到 doing（模拟员工开工）
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev", remark="开工")
        self.store.save_task(t)

        # 4. 验收（评分 4）
        status, body = self.client.post("/api/task/accept",
                                        {"id": tid, "score": 4, "comment": "不错"})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "done")
        # 出队
        self.assertNotIn(tid, queue_of(self.store, "dev"))
        # 经验回写（score>=3 → success）
        dev = self.store.load_employee("dev")
        self.assertEqual(dev.memory["experiences"][-1]["outcome"], "success")
        self.assertEqual(dev.memory["experiences"][-1]["task_type"], "清洗函数")
        # 账本记账（FileLedger 落盘）
        _, perf = self.client.get("/api/perf")
        self.assertGreaterEqual(perf["dev"]["completion_count"], base + 1)

    def test_accept_rejects_bad_state_and_score(self):
        # score 边界
        status, body = self.client.post("/api/task/accept", {"id": "X", "score": 0})
        self.assertEqual(status, 400)
        status, body = self.client.post("/api/task/accept", {"id": "X", "score": 6})
        self.assertEqual(status, 400)
        # pending 状态不可验收
        _, body = self.client.post("/api/task/submit", {"title": "占位"})
        status, body = self.client.post("/api/task/accept",
                                        {"id": body["id"], "score": 3})
        self.assertEqual(status, 409)

    def test_batch_assign_partial_success(self):
        """批量派发：部分成功语义 + 去重保序 + 逐条失败原因。"""
        from laoban.core.workstation import queue_of
        _, a = self.client.post("/api/task/submit", {"title": "批A"})
        _, b = self.client.post("/api/task/submit", {"title": "批B"})
        _, c = self.client.post("/api/task/submit", {"title": "批C"})
        # B 先单独派给 emp-chen（占位：批量派 B 必失败）
        self.client.post("/api/task/assign", {"id": b["id"], "to": "emp-chen"})
        status, body = self.client.post("/api/task/assign-batch",
                                        {"ids": [a["id"], b["id"], c["id"],
                                                 a["id"],   # 重复项应被去重
                                                 "T-nope"],  # 不存在的任务
                                         "to": "dev"})
        self.assertEqual(status, 200)
        self.assertEqual(body["ok_count"], 2)   # A、C 成功
        self.assertEqual(body["fail_count"], 2)  # B 状态不符 + T-nope 不存在
        self.assertEqual(body["assigned"], [a["id"], c["id"]])   # 保序
        by_id = {r["id"]: r for r in body["results"]}
        self.assertTrue(by_id[b["id"]]["ok"] is False)
        # 失败原因：B 已派给 emp-chen，再派触发非法状态转换（定位靠 id 字段）
        self.assertIn("assigned", by_id[b["id"]]["error"])
        self.assertIn("不存在", by_id["T-nope"]["error"])
        self.assertIn("2/4", body["message"])   # 汇总口径（去重后 4 条）
        # 工位队列：A、C 入 dev 队列；B 在 emp-chen 队列
        q = queue_of(self.store, "dev")
        self.assertIn(a["id"], q)
        self.assertIn(c["id"], q)
        self.assertNotIn(b["id"], q)
        self.assertIn(b["id"], queue_of(self.store, "emp-chen"))
        # 重复项只派一次
        self.assertEqual(q.count(a["id"]), 1)

    def test_batch_assign_validation_and_all_fail(self):
        """批量派发参数校验（400）与全失败（409）。"""
        # 参数校验
        for bad in ({}, {"ids": ["T-x"]}, {"to": "dev"}, {"to": "dev", "ids": []},
                    {"to": "dev", "ids": "T-x"}, {"to": "dev", "ids": ["  "]}):
            status, _ = self.client.post("/api/task/assign-batch", bad)
            self.assertEqual(status, 400, f"应 400：{bad}")
        # 全失败：两条都已派发 → 409 带逐条原因
        _, a = self.client.post("/api/task/submit", {"title": "已派1"})
        _, b = self.client.post("/api/task/submit", {"title": "已派2"})
        self.client.post("/api/task/assign", {"id": a["id"], "to": "dev"})
        self.client.post("/api/task/assign", {"id": b["id"], "to": "dev"})
        status, body = self.client.post("/api/task/assign-batch",
                                        {"ids": [a["id"], b["id"]], "to": "emp-chen"})
        self.assertEqual(status, 409)
        self.assertIn("全部失败", body["error"])
        self.assertIn(a["id"], body["error"])
        # 派给不存在的员工：单条即全失败（KeyError 员工不存在）
        _, c = self.client.post("/api/task/submit", {"title": "没人接"})
        status, body = self.client.post("/api/task/assign-batch",
                                        {"ids": [c["id"]], "to": "ghost"})
        self.assertEqual(status, 409)
        self.assertIn("不存在", body["error"])

    def test_low_score_records_failure(self):
        _, sub = self.client.post("/api/task/submit", {"title": "低分任务"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev")
        self.store.save_task(t)
        self.client.post("/api/task/accept", {"id": tid, "score": 1})
        dev = self.store.load_employee("dev")
        self.assertEqual(dev.memory["experiences"][-1]["outcome"], "failure")

    def test_low_score_reworks_task(self):
        """低分驳回（未超限）：任务回炉 assigned 等重做，不记完成、扣驳回分。"""
        from laoban.core.workstation import queue_of
        _, perf0 = self.client.get("/api/perf")
        base_done = perf0.get("dev", {}).get("completion_count", 0)
        _, sub = self.client.post("/api/task/submit", {"title": "返工任务"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev")
        self.store.save_task(t)
        status, body = self.client.post("/api/task/accept", {"id": tid, "score": 1})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "assigned")   # 回炉而非结案
        self.assertIn("驳回返工", body["message"])
        # 任务留在队列（等重做），返工轮次已计
        self.assertIn(tid, queue_of(self.store, "dev"))
        self.assertEqual(self.store.load_task(tid).review_round, 1)
        # 不记完成（没通过不算交付），记驳回
        _, perf = self.client.get("/api/perf")
        self.assertEqual(perf["dev"]["completion_count"], base_done)
        self.assertGreaterEqual(perf["dev"]["rejection_count"], 1)

    def test_rework_exceeds_rounds_force_closes(self):
        """返工超限（3 轮）：再驳回即强制结案，任务出队。"""
        from laoban.core.workstation import queue_of
        _, sub = self.client.post("/api/task/submit", {"title": "多次返工"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        for _ in range(3):   # 三轮返工
            t = self.store.load_task(tid)
            advance(t, DOING, actor="dev")
            self.store.save_task(t)
            status, body = self.client.post("/api/task/accept", {"id": tid, "score": 1})
            self.assertEqual(status, 200)
            self.assertEqual(body["state"], "assigned")
        self.assertEqual(self.store.load_task(tid).review_round, 3)
        # 第四轮低分：超限 → 强制结案
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev")
        self.store.save_task(t)
        status, body = self.client.post("/api/task/accept", {"id": tid, "score": 1})
        self.assertEqual(body["state"], "done")
        self.assertNotIn(tid, queue_of(self.store, "dev"))

    def test_human_report_flow(self):
        """人类任务汇报：assigned → reporting，之后验收闭环打通。"""
        _, sub = self.client.post("/api/task/submit", {"title": "人工核查"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "emp-chen"})
        status, body = self.client.post(
            "/api/task/report",
            {"id": tid, "deliverable": "已核对 200 行数据，3 处异常已修正"})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "reporting")
        # 交付物落档（验收成本口径取最新一条）
        t = self.store.load_task(tid)
        self.assertEqual(t.progress_log[-1]["by"], "emp-chen")
        self.assertIn("3 处异常", t.progress_log[-1]["deliverable"])
        # 验收闭环（人类任务也能验收了）
        status, body = self.client.post("/api/task/accept", {"id": tid, "score": 5})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "done")

    def test_report_rejects_ai_task(self):
        """AI 任务不走人工汇报口（由 worker 自动执行）。"""
        _, sub = self.client.post("/api/task/submit", {"title": "AI 任务"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        status, _ = self.client.post("/api/task/report",
                                     {"id": tid, "deliverable": "x"})
        self.assertEqual(status, 409)

    def test_report_rejects_bad_state(self):
        _, sub = self.client.post("/api/task/submit", {"title": "未派单"})
        status, _ = self.client.post("/api/task/report",
                                     {"id": sub["id"], "deliverable": "x"})
        self.assertEqual(status, 409)   # pending 不可汇报
        status, _ = self.client.post("/api/task/report",
                                     {"id": "X", "deliverable": "x"})
        self.assertEqual(status, 404)

    def test_retry_revives_blocked_task(self):
        """死单复活：blocked 任务一键重试，重入队列等 worker 再跑。"""
        from laoban.core.workstation import queue_of
        from laoban.core.task import DOING, BLOCKED as BLK
        _, sub = self.client.post("/api/task/submit", {"title": "失败任务"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev")
        advance(t, BLK, actor="worker", remark="自动执行失败：LLM 服务不可用")
        t.block_reason = "自动执行失败：LLM 服务不可用"
        self.store.save_task(t)
        # worker 的 blocked 善后会出队——模拟之（retry 的 enqueue 幂等）
        from laoban.core.workstation import dequeue
        dequeue(self.store, "dev", tid)
        status, body = self.client.post("/api/task/retry", {"id": tid})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "assigned")
        self.assertIn("重试", body["message"])
        # 重入队列 + 轮次计 1 + 阻塞原因清除
        self.assertIn(tid, queue_of(self.store, "dev"))
        t = self.store.load_task(tid)
        self.assertEqual(t.review_round, 1)
        self.assertEqual(t.block_reason, "")
        # 复活后可再走执行流
        advance(t, DOING, actor="dev")
        self.store.save_task(t)

    def test_retry_rejects_non_blocked(self):
        _, sub = self.client.post("/api/task/submit", {"title": "未阻塞"})
        status, body = self.client.post("/api/task/retry", {"id": sub["id"]})
        self.assertEqual(status, 409)   # pending 不可重试
        status, _ = self.client.post("/api/task/retry", {"id": "X"})
        self.assertEqual(status, 404)

    def test_urge_overdue_task_sends_message(self):
        """催办闭环：超期在飞任务一键催办 → 承接人收件箱收到催办信。"""
        from laoban.core.messenger import inbox
        _, sub = self.client.post("/api/task/submit",
                                  {"title": "拖了三年的活",
                                   "due_at": "2023-01-01"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "emp-chen"})
        status, body = self.client.post("/api/task/urge", {"id": tid})
        self.assertEqual(status, 200)
        self.assertEqual(body["assignee"], "emp-chen")
        self.assertIn("已催办", body["message"])
        # 承接人收件箱：催办信带任务 id 与超期说明
        box = inbox(self.store, "emp-chen")
        hit = [m for m in box if m.get("task_id") == tid and "催办" in m["content"]]
        self.assertEqual(len(hit), 1)
        self.assertIn("超期", hit[0]["content"])

    def test_urge_rejects_not_overdue_or_wrong_state(self):
        # 未超期（截止在未来）
        _, sub = self.client.post("/api/task/submit",
                                  {"title": "还没到期的活",
                                   "due_at": "2099-01-01"})
        self.client.post("/api/task/assign", {"id": sub["id"], "to": "emp-chen"})
        status, body = self.client.post("/api/task/urge", {"id": sub["id"]})
        self.assertEqual(status, 409)
        self.assertIn("尚未超期", body["error"])
        # 无截止
        _, sub2 = self.client.post("/api/task/submit", {"title": "无限期"})
        self.client.post("/api/task/assign", {"id": sub2["id"], "to": "emp-chen"})
        status, body = self.client.post("/api/task/urge", {"id": sub2["id"]})
        self.assertEqual(status, 409)
        self.assertIn("未设截止", body["error"])
        # 待分拣（不在飞）
        _, sub3 = self.client.post("/api/task/submit", {"title": "未派发"})
        status, _ = self.client.post("/api/task/urge", {"id": sub3["id"]})
        self.assertEqual(status, 409)
        # 不存在
        status, _ = self.client.post("/api/task/urge", {"id": "X"})
        self.assertEqual(status, 404)

    def test_payroll_monthly_report(self):
        """月度绩效报表：JSON 周期统计 + CSV 导出（发薪口径）。"""
        # 先产生一笔本月记账：提交→派单→验收
        _, sub = self.client.post("/api/task/submit", {"title": "月报口径"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev")
        self.store.save_task(t)
        self.client.post("/api/task/accept", {"id": tid, "score": 4})
        base = self.client.get("/api/perf")[1]["dev"]["completion_count"]

        status, body = self.client.get("/api/report/payroll")
        self.assertEqual(status, 200)
        self.assertIn("rows", body)
        by_id = {r["id"]: r for r in body["rows"]}
        self.assertIn("dev", by_id)
        self.assertGreaterEqual(by_id["dev"]["completion_count"], 1)
        self.assertEqual(by_id["dev"]["name"], "阿码")
        # 全员都在（含当月 0 记录的——发薪表要看全名单）
        self.assertIn("emp-wang", by_id)
        self.assertEqual(by_id["emp-wang"]["completion_count"], 0)

        # CSV：BOM + 表头 + 数据行 + 附件下载头
        import urllib.request
        req = urllib.request.Request(
            f"{self.client.base}/api/report/payroll.csv")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/csv", r.headers["Content-Type"])
            self.assertIn("attachment", r.headers["Content-Disposition"])
            raw = r.read().decode("utf-8-sig")
        lines = raw.strip().splitlines()
        self.assertEqual(lines[0].split(",")[:2], ["员工ID", "姓名"])
        self.assertTrue(any(l.startswith("dev,") for l in lines[1:]))
        # 非法月份
        status, _ = self.client.get("/api/report/payroll?month=2026-13")
        self.assertEqual(status, 400)
        # 合法指定月份（当月）
        import datetime as _dt
        this = _dt.date.today().strftime("%Y-%m")
        status, body = self.client.get(f"/api/report/payroll?month={this}")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(
            {r["id"]: r for r in body["rows"]}["dev"]["completion_count"], 1)

    def test_workload_view(self):
        """负荷视图：在办/待执行/执行中/待验收/超期/今日完成计数 + 排序。"""
        from laoban.core.task import REPORTING
        # 基线（增量断言，避免依赖同类其他用例留下的任务）
        _, rows0 = self.client.get("/api/workload")
        base = {r["id"]: r for r in rows0}
        # dev：+1 执行中（doing）
        _, s1 = self.client.post("/api/task/submit", {"title": "执行中的活"})
        self.client.post("/api/task/assign", {"id": s1["id"], "to": "dev"})
        t = self.store.load_task(s1["id"])
        advance(t, DOING, actor="dev")
        self.store.save_task(t)
        # dev：+1 超期待验收（doing → reporting，仍在队列）
        _, s2 = self.client.post("/api/task/submit",
                                 {"title": "超期交付", "due_at": "2023-01-01"})
        self.client.post("/api/task/assign", {"id": s2["id"], "to": "dev"})
        t2 = self.store.load_task(s2["id"])
        advance(t2, DOING, actor="dev")
        advance(t2, REPORTING, actor="dev")
        self.store.save_task(t2)
        # emp-chen：+1 待执行（assigned 在队列排队）
        _, s3 = self.client.post("/api/task/submit", {"title": "排队中的活"})
        self.client.post("/api/task/assign", {"id": s3["id"], "to": "emp-chen"})
        # dev：+1 今日完成（验收出队）
        _, s4 = self.client.post("/api/task/submit", {"title": "今日完成的活"})
        self.client.post("/api/task/assign", {"id": s4["id"], "to": "dev"})
        t4 = self.store.load_task(s4["id"])
        advance(t4, DOING, actor="dev")
        self.store.save_task(t4)
        self.client.post("/api/task/accept", {"id": s4["id"], "score": 4})

        status, rows = self.client.get("/api/workload")
        self.assertEqual(status, 200)
        by = {r["id"]: r for r in rows}
        # dev：在办 +2（doing + reporting 均占队列）、待验收 +1、超期 +1、今日完成 +1
        for key, delta in (("queue", 2), ("assigned", 0), ("doing", 1),
                           ("reporting", 1), ("overdue", 1), ("done_today", 1)):
            self.assertEqual(by["dev"][key], base["dev"][key] + delta,
                             f"dev.{key}")
        # emp-chen：在办 +1，全是待执行
        self.assertEqual(by["emp-chen"]["queue"],
                         base["emp-chen"]["queue"] + 1)
        self.assertEqual(by["emp-chen"]["assigned"],
                         base["emp-chen"]["assigned"] + 1)
        # 排序：在职优先 → 在办总数升序（第一行 = 最闲）
        active_queues = [r["queue"] for r in rows if r["status"] == "active"]
        self.assertEqual(active_queues, sorted(active_queues))
        # 非在职垫底（王姐停职后最忙也排最后）
        wang = self.store.load_employee("emp-wang")
        wang.status = "suspended"
        self.store.save_employee(wang)
        _, rows2 = self.client.get("/api/workload")
        self.assertEqual(rows2[-1]["id"], "emp-wang")

    def test_file_ledger_persists(self):
        """记账后新 server 实例（模拟重启）能读到旧账。"""
        # 本测试自证：先走一遍完整验收产生账目
        _, sub = self.client.post("/api/task/submit", {"title": "重启账目"})
        tid = sub["id"]
        self.client.post("/api/task/assign", {"id": tid, "to": "dev"})
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev")
        self.store.save_task(t)
        self.client.post("/api/task/accept", {"id": tid, "score": 5})
        _, perf = self.client.get("/api/perf")
        before = perf["dev"]["completion_count"]
        # 模拟重启：新实例从磁盘加载账本
        s2 = DashboardServer(self.store, port=0)
        threading.Thread(target=s2.serve_forever, daemon=True).start()
        try:
            c2 = _Client(f"http://127.0.0.1:{s2.port}")
            status, perf = c2.get("/api/perf")
            self.assertEqual(status, 200)
            self.assertEqual(perf["dev"]["completion_count"], before)
        finally:
            s2.shutdown()


class TestOpsRbac(unittest.TestCase):
    """操作端点角色守卫。"""

    @classmethod
    def setUpClass(cls):
        cls.store, cls.auth = _mk_auth_store()
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

    def test_staff_cannot_assign(self):
        c = self._login("emp-chen", "pw-chen")
        _, sub = c.post("/api/task/submit", {"title": "员工提的任务"})
        status, body = c.post("/api/task/assign", {"id": sub["id"], "to": "dev"})
        self.assertEqual(status, 403)
        # 员工可以提交
        self.assertEqual(sub["state"], "pending")

    def test_staff_cannot_retry(self):
        a = self._login("boss", "pw-boss")
        _, sub = a.post("/api/task/submit", {"title": "要死的任务"})
        tid = sub["id"]
        a.post("/api/task/assign", {"id": tid, "to": "dev"})
        t = self.store.load_task(tid)
        advance(t, DOING, actor="dev")
        advance(t, "blocked", actor="worker", remark="失败")
        self.store.save_task(t)
        c = self._login("emp-chen", "pw-chen")
        status, _ = c.post("/api/task/retry", {"id": tid})
        self.assertEqual(status, 403)

    def test_staff_cannot_urge(self):
        a = self._login("boss", "pw-boss")
        _, sub = a.post("/api/task/submit",
                        {"title": "要催的任务", "due_at": "2023-01-01"})
        tid = sub["id"]
        a.post("/api/task/assign", {"id": tid, "to": "emp-chen"})
        c = self._login("emp-chen", "pw-chen")
        status, _ = c.post("/api/task/urge", {"id": tid})
        self.assertEqual(status, 403)
        # 老板可催，催办信发件人是老板本人
        status, body = a.post("/api/task/urge", {"id": tid})
        self.assertEqual(status, 200)
        from laoban.core.messenger import inbox
        hit = [m for m in inbox(self.store, "emp-chen")
               if m.get("task_id") == tid]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["from"], "boss")

    def test_staff_cannot_batch_assign(self):
        a = self._login("boss", "pw-boss")
        _, sub = a.post("/api/task/submit", {"title": "员工想批派"})
        c = self._login("emp-chen", "pw-chen")
        status, body = c.post("/api/task/assign-batch",
                              {"ids": [sub["id"]], "to": "dev"})
        self.assertEqual(status, 403)
        self.assertIn("仅管理员或部门负责人", body["error"])

    def test_manager_batch_assign_dept_scope(self):
        """manager 批量派发：本部门 OK；目标为外部门员工 → 403。"""
        a = self._login("boss", "pw-boss")
        _, s1 = a.post("/api/task/submit", {"title": "本部门批1"})
        _, s2 = a.post("/api/task/submit", {"title": "本部门批2"})
        m = self._login("mgr-dev", "pw-mgr")
        status, body = m.post("/api/task/assign-batch",
                              {"ids": [s1["id"], s2["id"]], "to": "dev"})
        self.assertEqual(status, 200)
        self.assertEqual(body["ok_count"], 2)
        from laoban.core.workstation import queue_of
        for tid in (s1["id"], s2["id"]):
            self.assertIn(tid, queue_of(self.store, "dev"))
        # 跨部门承接人：fin_dept 的 fin → 403（整批拒，一条都不动）
        _, s3 = a.post("/api/task/submit", {"title": "外部门目标"})
        status, body = m.post("/api/task/assign-batch",
                              {"ids": [s3["id"]], "to": "fin"})
        self.assertEqual(status, 403)
        self.assertEqual(self.store.load_task(s3["id"]).state, "pending")
        # 派给自己的部门成员含自己也可以（mgr-dev 是 dev_dept 成员）
        _, s4 = a.post("/api/task/submit", {"title": "派给自己"})
        status, body = m.post("/api/task/assign-batch",
                              {"ids": [s4["id"]], "to": "mgr-dev"})
        self.assertEqual(status, 200)
        self.assertIn(s4["id"], queue_of(self.store, "mgr-dev"))

    def test_payroll_scope_by_role(self):
        """月报可见范围：admin 全公司 / manager 本部门 / staff 仅本人。"""
        c = self._login("boss", "pw-boss")
        status, body = c.get("/api/report/payroll")
        self.assertEqual(status, 200)
        ids = {r["id"] for r in body["rows"]}
        self.assertTrue(ids >= {"boss", "mgr-dev", "dev", "emp-chen",
                                "emp-xiaoli", "fin", "emp-wang"})
        m = self._login("mgr-dev", "pw-mgr")
        _, body = m.get("/api/report/payroll")
        self.assertEqual({r["id"] for r in body["rows"]},
                         {"mgr-dev", "dev", "emp-chen", "emp-xiaoli"})
        s = self._login("emp-chen", "pw-chen")
        _, body = s.get("/api/report/payroll")
        self.assertEqual({r["id"] for r in body["rows"]}, {"emp-chen"})

    def test_workload_scope_by_role(self):
        """负荷可见范围：admin 全公司 / manager 本部门 / staff 本部门。"""
        c = self._login("boss", "pw-boss")
        status, rows = c.get("/api/workload")
        self.assertEqual(status, 200)
        self.assertTrue({r["id"] for r in rows} >=
                        {"boss", "mgr-dev", "dev", "emp-chen", "emp-wang"})
        m = self._login("mgr-dev", "pw-mgr")
        _, rows = m.get("/api/workload")
        self.assertEqual({r["id"] for r in rows},
                         {"mgr-dev", "dev", "emp-chen", "emp-xiaoli"})
        s = self._login("emp-chen", "pw-chen")
        _, rows = s.get("/api/workload")
        # staff：本部门口径（同花名册，均为计数不含敏感字段）
        self.assertEqual({r["id"] for r in rows},
                         {"mgr-dev", "dev", "emp-chen", "emp-xiaoli"})

    def test_manager_assign_only_own_dept(self):
        m = self._login("mgr-dev", "pw-mgr")
        _, sub = m.post("/api/task/submit", {"title": "负责人提的任务"})
        status, _ = m.post("/api/task/assign", {"id": sub["id"], "to": "dev"})
        self.assertEqual(status, 200)          # 本部门 OK
        _, sub2 = m.post("/api/task/submit", {"title": "再提一个"})
        status, _ = m.post("/api/task/assign", {"id": sub2["id"], "to": "emp-wang"})
        self.assertEqual(status, 403)          # 跨部门拒

    def test_manager_accept_cross_dept_denied(self):
        m = self._login("mgr-dev", "pw-mgr")
        a = self._login("boss", "pw-boss")
        _, sub = a.post("/api/task/submit", {"title": "财务任务"})
        a.post("/api/task/assign", {"id": sub["id"], "to": "emp-wang"})
        t = self.store.load_task(sub["id"])
        advance(t, DOING, actor="emp-wang")
        self.store.save_task(t)
        status, _ = m.post("/api/task/accept", {"id": sub["id"], "score": 3})
        self.assertEqual(status, 403)          # 承接人不在本部门

    def test_approval_admin_only_and_ledger(self):
        # 造一张待审批单
        log = ApprovalLog(self.store)
        req = ApprovalRequest(id="AP-test001", type="高危操作", risk="high",
                              requester="dev", summary="删库")
        log.log_request(req)
        # staff 决策 → 403
        c = self._login("emp-chen", "pw-chen")
        status, _ = c.post("/api/approval/decide",
                           {"id": "AP-test001", "approved": True})
        self.assertEqual(status, 403)
        # admin 决策 → 通过
        a = self._login("boss", "pw-boss")
        status, body = a.post("/api/approval/decide",
                              {"id": "AP-test001", "approved": True, "opinion": "准"})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "approved")
        # 重复决策 → 404
        status, _ = a.post("/api/approval/decide", {"id": "AP-test001", "approved": False})
        self.assertEqual(status, 404)
        # 账本：requester 记了人类介入
        _, perf = a.get("/api/perf")
        self.assertEqual(perf["dev"]["human_intervention_rate"], 1.0)

    def test_approvals_visibility(self):
        log = ApprovalLog(self.store)
        log.log_request(ApprovalRequest(id="AP-vis001", type="高危操作",
                                        risk="high", requester="emp-wang",
                                        summary="测试可见性"))
        c = self._login("emp-chen", "pw-chen")
        status, mine = c.get("/api/approvals?status=pending")
        self.assertEqual(status, 200)
        self.assertEqual(mine, [])              # 别人的单看不到
        a = self._login("boss", "pw-boss")
        _, all_ = a.get("/api/approvals?status=pending")
        ids = {e["id"] for e in all_}
        self.assertIn("AP-vis001", ids)         # admin 全见

    def test_staff_report_only_own_task(self):
        """人类汇报权限：staff 只能报自己的；manager 可代报本部门成员。"""
        a = self._login("boss", "pw-boss")
        _, own = a.post("/api/task/submit", {"title": "给陈工"})
        a.post("/api/task/assign", {"id": own["id"], "to": "emp-chen"})
        _, other = a.post("/api/task/submit", {"title": "给王姐"})
        a.post("/api/task/assign", {"id": other["id"], "to": "emp-wang"})
        # staff 报别人的任务 → 403
        c = self._login("emp-chen", "pw-chen")
        status, _ = c.post("/api/task/report",
                           {"id": other["id"], "deliverable": "代报"})
        self.assertEqual(status, 403)
        # 报自己的 → 200
        status, body = c.post("/api/task/report",
                              {"id": own["id"], "deliverable": "陈工已完成"})
        self.assertEqual(status, 200)
        # manager 代报本部门成员（小李）→ 200
        _, xiaoli = a.post("/api/task/submit", {"title": "给小李"})
        a.post("/api/task/assign", {"id": xiaoli["id"], "to": "emp-xiaoli"})
        m = self._login("mgr-dev", "pw-mgr")
        status, _ = m.post("/api/task/report",
                           {"id": xiaoli["id"], "deliverable": "代小李报"})
        self.assertEqual(status, 200)
        # manager 代报跨部门（王姐）→ 403
        _, wang2 = a.post("/api/task/submit", {"title": "再给王姐"})
        a.post("/api/task/assign", {"id": wang2["id"], "to": "emp-wang"})
        status, _ = m.post("/api/task/report",
                           {"id": wang2["id"], "deliverable": "越权代报"})
        self.assertEqual(status, 403)

    def test_perf_visibility(self):
        # 给 dev 记一票账（走完整验收），再看 staff 视角是否看不到 dev
        a = self._login("boss", "pw-boss")
        _, sub = a.post("/api/task/submit", {"title": "绩效可见性"})
        a.post("/api/task/assign", {"id": sub["id"], "to": "dev"})
        t = self.store.load_task(sub["id"])
        advance(t, DOING, actor="dev")
        self.store.save_task(t)
        a.post("/api/task/accept", {"id": sub["id"], "score": 4})
        # staff 视角：不含 dev（只可能有自己的账）
        c = self._login("emp-chen", "pw-chen")
        _, perf = c.get("/api/perf")
        self.assertNotIn("dev", perf)
        # admin 视角：含 dev
        _, perf = a.get("/api/perf")
        self.assertIn("dev", perf)


class TestAssignAuto(unittest.TestCase):
    """快捷派发：pending 直达 assigned，中间流转留痕。"""

    def test_pending_direct_assign(self):
        st = _mk_free_store()
        st.save_task(Task(id="T-A1", title="直派"))
        t = assign_task_auto(st, "T-A1", "dev", actor="boss")
        self.assertEqual(t.state, "assigned")
        states = [log["to"] for log in t.flow_log]
        self.assertEqual(states, ["triage", "planning", "review", "assigned"])
        # 每步留痕
        remarks = [log.get("remark", "") for log in t.flow_log]
        self.assertIn("直派快捷", remarks[0])
        # 入队
        from laoban.core.workstation import queue_of
        self.assertIn("T-A1", queue_of(st, "dev"))

    def test_review_state_normal_assign(self):
        st = _mk_free_store()
        t = Task(id="T-R1", title="已评审")
        for s in (TRIAGE, PLANNING, REVIEW):
            advance(t, s, actor="pm")
        st.save_task(t)
        t2 = assign_task_auto(st, "T-R1", "dev", actor="boss")
        self.assertEqual(t2.state, "assigned")
        # 不重复走快捷流程
        self.assertEqual(len(t2.flow_log), 4)

    def test_assigned_task_reassign_raises(self):
        st = _mk_free_store()
        st.save_task(Task(id="T-D1", title="已派"))
        assign_task_auto(st, "T-D1", "dev")
        from laoban.core.state_machine import IllegalTransition
        with self.assertRaises(IllegalTransition):
            assign_task_auto(st, "T-D1", "dev")   # assigned 不能再派


if __name__ == "__main__":
    unittest.main()
