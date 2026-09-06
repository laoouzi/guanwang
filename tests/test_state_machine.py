import unittest
from laoban.core.task import (
    Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING,
    WAITING_HUMAN, REPORTING, DONE, CANCELLED, BLOCKED,
)
from laoban.core.state_machine import (
    advance, can_transition, IllegalTransition, MAX_REVIEW_ROUNDS,
)


class TestStateMachine(unittest.TestCase):
    def test_happy_path(self):
        t = Task(id="T-1", title="x")
        for s in [TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, REPORTING, DONE]:
            advance(t, s, actor="boss")
        self.assertEqual(t.state, DONE)
        self.assertEqual(len(t.flow_log), 7)

    def test_illegal_jump_rejected(self):
        t = Task(id="T-1", title="x")  # pending
        with self.assertRaises(IllegalTransition):
            advance(t, DOING)

    def test_terminal_blocks_advance(self):
        t = Task(id="T-1", title="x", state=DONE)
        ok, _ = can_transition(t, TRIAGE)
        self.assertFalse(ok)

    def test_cancel_anywhere(self):
        t = Task(id="T-1", title="x", state=DOING)
        advance(t, CANCELLED)
        self.assertEqual(t.state, CANCELLED)

    def test_reject_increments_round(self):
        t = Task(id="T-1", title="x", state=REVIEW)
        advance(t, PLANNING, actor="reviewer", remark="封驳")
        self.assertEqual(t.review_round, 1)
        self.assertEqual(t.state, PLANNING)

    def test_reject_beyond_max_rounds(self):
        t = Task(id="T-1", title="x", state=REVIEW, review_round=MAX_REVIEW_ROUNDS)
        with self.assertRaises(IllegalTransition):
            advance(t, PLANNING)

    def test_reject_rework_returns_to_assigned(self):
        """验收驳回返工：reporting → assigned，计入返工轮次。"""
        t = Task(id="T-1", title="x", state=DOING)
        advance(t, REPORTING)
        advance(t, ASSIGNED, actor="boss", remark="驳回返工")
        self.assertEqual(t.state, ASSIGNED)
        self.assertEqual(t.review_round, 1)
        # 回炉后可重新开工
        advance(t, DOING)
        advance(t, REPORTING)

    def test_rework_beyond_max_rounds(self):
        t = Task(id="T-1", title="x", state=REPORTING,
                 review_round=MAX_REVIEW_ROUNDS)
        with self.assertRaises(IllegalTransition):
            advance(t, ASSIGNED)

    def test_waiting_human_roundtrip(self):
        t = Task(id="T-1", title="x", state=DOING)
        advance(t, WAITING_HUMAN)
        advance(t, DOING)
        self.assertEqual(t.state, DOING)

    def test_blocked_retry_revives_to_assigned(self):
        """死单复活：blocked → assigned 重试，计入轮次（复用上限防无限重试）。"""
        t = Task(id="T-1", title="x", state=DOING)
        advance(t, BLOCKED, actor="worker", remark="LLM 失败")
        advance(t, ASSIGNED, actor="boss", remark="重试")
        self.assertEqual(t.state, ASSIGNED)
        self.assertEqual(t.review_round, 1)
        # 复活后可重新走执行流
        advance(t, DOING)
        advance(t, REPORTING)

    def test_blocked_retry_beyond_max_rounds(self):
        t = Task(id="T-1", title="x", state=BLOCKED,
                 review_round=MAX_REVIEW_ROUNDS)
        ok, reason = can_transition(t, ASSIGNED)
        self.assertFalse(ok)
        self.assertIn("重试超限", reason)
        with self.assertRaises(IllegalTransition):
            advance(t, ASSIGNED)

    def test_blocked_to_other_states_still_illegal(self):
        """blocked 只能复活到 assigned：去 done/doing 仍然非法。"""
        t = Task(id="T-1", title="x", state=BLOCKED)
        for s in (DOING, REPORTING, DONE, PENDING):
            with self.assertRaises(IllegalTransition):
                advance(t, s)


if __name__ == "__main__":
    unittest.main()
