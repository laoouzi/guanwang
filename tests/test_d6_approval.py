"""D6：高危操作 100% 必有审批记录（CI 自动化断言）。"""
from __future__ import annotations

import tempfile
import unittest

from laoban.core.store import JsonStore
from laoban.core.employee import Employee
from laoban.runner.approval_queue import ApprovalQueue, ApprovalRequest
from laoban.runner.approval_log import (
    ApprovalLog, ApprovalLogEntry, request_and_maybe_block,
)


class TestD6ApprovalAudit(unittest.TestCase):
    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        self.log = ApprovalLog(self.store)
        self.queue = ApprovalQueue()

    # ── 核心 D6 断言 ─────────────────────────────────────────────────
    def test_high_risk_command_always_has_log(self):
        """D6: 高危操作（shell_exec）默认配置下必有审批记录。"""
        emp = Employee(id="dev", name="阿码",
                       permissions={"autonomy_level": "supervised"})
        need, log_id, reason = request_and_maybe_block(
            emp, "shell_exec", {"cmd": "python setup.py install"},
            self.queue, self.log,
        )
        self.assertTrue(need, msg=reason)
        # 100% 有落盘记录
        self.assertEqual(len(self.log.list_logs(risk="high")), 1)
        entry = self.log.list_logs(risk="high")[0]
        self.assertEqual(entry.request["requester"], "dev")
        self.assertEqual(entry.request["type"], "高危操作")

    def test_high_risk_file_write_outside_workspace(self):
        """D6: 写文件到 workspaces 外 = high，必产生记录。"""
        emp = Employee(id="dev", name="阿码",
                       permissions={"autonomy_level": "full"})  # 最高自主
        need, log_id, reason = request_and_maybe_block(
            emp, "file_rw", {"path": "/etc/passwd-fake", "action": "write"},
            self.queue, self.log,
        )
        # high 永远需要审批，就算 full 也不放行
        self.assertTrue(need, msg=f"full autonomy high 仍必须审批：{reason}")
        self.assertEqual(len(self.log.list_logs(risk="high")), 1)

    def test_low_risk_inside_workspace_full_auto_approve(self):
        """workspaces 内写 = low，full 自主自动放行，但仍有审计日志。"""
        emp = Employee(id="dev", name="阿码",
                       permissions={"autonomy_level": "full"})
        need, log_id, reason = request_and_maybe_block(
            emp, "file_rw", {"path": "workspaces/dev/foo.py", "action": "read"},
            self.queue, self.log,
        )
        self.assertFalse(need)
        # 记一笔备查（审计链完整）
        entries = self.log.list_logs()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].request["risk"], "low")

    def test_decision_writes_status_and_approver(self):
        """审批结果回写，status/approver 可被审计。"""
        emp = Employee(id="dev", name="阿码")
        _, log_id, _ = request_and_maybe_block(
            emp, "shell_exec", {"cmd": "run"}, self.queue, self.log,
        )
        self.log.log_decision(log_id, approver="boss", approved=True,
                              opinion="紧急操作，批准一次")
        [entry] = self.log.list_logs(status="approved")
        self.assertEqual(entry.approver, "boss")
        self.assertIn("紧急", entry.opinion)
        self.assertEqual(entry.request["status"], "approved")

    def test_filter_logs_by_multiple_conditions(self):
        e1 = Employee(id="dev", name="阿码")
        e2 = Employee(id="pm", name="老谋")
        for emp in (e1, e1, e2):
            request_and_maybe_block(emp, "shell_exec", {"cmd": "x"}, self.queue, self.log)
        # dev 的 high 风险 = 2 条
        self.assertEqual(len(self.log.list_logs(requester="dev", risk="high")), 2)
        # pm 的 = 1 条
        self.assertEqual(len(self.log.list_logs(requester="pm")), 1)
        # 全部 high = 3 条
        self.assertEqual(len(self.log.list_logs(risk="high")), 3)

    def test_no_log_without_log_request(self):
        """没走 log_request 就没有记录——反证 D6 依赖统一入口。"""
        self.assertEqual(len(self.log.list_logs()), 0)


if __name__ == "__main__":
    unittest.main()
