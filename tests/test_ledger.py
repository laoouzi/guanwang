import unittest
from laoban.core.ledger import Ledger
from laoban.core.employee import Employee
from laoban.core.feedback import write_back_experience


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


class TestFeedback(unittest.TestCase):
    def test_write_back(self):
        emp = Employee(id="dev", name="阿码")
        write_back_experience(emp, task_type="bugfix", score=4, comment="先读测试")
        self.assertEqual(len(emp.memory["experiences"]), 1)
        self.assertEqual(emp.memory["experiences"][0]["outcome"], "success")

    def test_low_score_marked_failure(self):
        emp = Employee(id="dev", name="阿码")
        write_back_experience(emp, task_type="bugfix", score=1, comment="")
        self.assertEqual(emp.memory["experiences"][0]["outcome"], "failure")


if __name__ == "__main__":
    unittest.main()
