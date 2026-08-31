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
