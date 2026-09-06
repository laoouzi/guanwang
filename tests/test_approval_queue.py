import unittest
from laoban.runner.approval_queue import ApprovalQueue, ApprovalRequest, should_approve


class TestShouldApprove(unittest.TestCase):
    def test_high_risk_always_approve(self):
        self.assertTrue(should_approve("high", "full"))
        self.assertTrue(should_approve("high", "supervised"))

    def test_full_autonomy_low_medium_passthrough(self):
        self.assertFalse(should_approve("low", "full"))
        self.assertFalse(should_approve("medium", "full"))

    def test_semi_medium_approve(self):
        self.assertFalse(should_approve("low", "semi"))
        self.assertTrue(should_approve("medium", "semi"))

    def test_supervised_all_approve(self):
        self.assertTrue(should_approve("low", "supervised"))
        self.assertTrue(should_approve("medium", "supervised"))


class TestApprovalQueue(unittest.TestCase):
    def test_capacity_trigger(self):
        q = ApprovalQueue(batch_size=2, timeout_sec=9999)
        q.enqueue(ApprovalRequest(id="a", type="支出超限", risk="high"))
        self.assertEqual(q.flush_if_ready(), [])
        q.enqueue(ApprovalRequest(id="b", type="支出超限", risk="high"))
        batch = q.flush_if_ready()
        self.assertEqual([r.id for r in batch], ["a", "b"])

    def test_time_trigger(self):
        q = ApprovalQueue(batch_size=99, timeout_sec=0)
        q.enqueue(ApprovalRequest(id="a", type="支出超限", risk="high"))
        batch = q.flush_if_ready()
        self.assertEqual([r.id for r in batch], ["a"])

    def test_urgent_priority_channel(self):
        q = ApprovalQueue(batch_size=99, timeout_sec=9999, urgent_batch_size=1)
        q.enqueue(ApprovalRequest(id="u", type="高危操作", risk="high", priority="urgent"))
        batch = q.flush_if_ready()
        self.assertEqual([r.id for r in batch], ["u"])


if __name__ == "__main__":
    unittest.main()
