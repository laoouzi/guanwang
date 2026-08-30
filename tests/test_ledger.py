import unittest
from laoban.core.ledger import Ledger


class TestLedger(unittest.TestCase):
    def test_completion_stats(self):
        lg = Ledger()
        lg.record_completion("dev", task_id="T-1", cost=1.0, elapsed=10)
        lg.record_completion("dev", task_id="T-2", cost=2.0, elapsed=20)
        s = lg.stats("dev")
        self.assertEqual(s["completion_count"], 2)
        self.assertEqual(s["total_cost"], 3.0)
        self.assertEqual(s["avg_elapsed"], 15.0)

    def test_rejection_rate(self):
        lg = Ledger()
        lg.record_completion("dev", task_id="T-1")
        lg.record_rejection("dev")
        lg.record_rejection("dev")
        s = lg.stats("dev")
        self.assertEqual(s["rejection_rate"], 2 / 3)  # 驳回 2 次 / 总评审 3 次

    def test_human_intervention_rate(self):
        lg = Ledger()
        for _ in range(10):
            lg.record_step("dev")
        lg.record_human_intervention("dev", "approval")
        lg.record_human_intervention("dev", "human_task")
        s = lg.stats("dev")
        self.assertEqual(s["human_intervention_rate"], 0.2)

    def test_unknown_employee_zero(self):
        lg = Ledger()
        s = lg.stats("nobody")
        self.assertEqual(s["completion_count"], 0)
        self.assertEqual(s["human_intervention_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
