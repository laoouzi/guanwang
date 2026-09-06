import unittest
from laoban.core.task import Task, PENDING, DONE, TERMINAL_STATES, utcnow


class TestTaskModel(unittest.TestCase):
    def test_defaults(self):
        t = Task(id="T-1", title="写函数")
        self.assertEqual(t.state, PENDING)
        self.assertEqual(t.priority, "normal")
        self.assertEqual(t.review_round, 0)
        self.assertEqual(t.flow_log, [])
        self.assertEqual(t.progress_log, [])

    def test_roundtrip(self):
        t = Task(id="T-1", title="写函数", state=DONE, review_round=2)
        d = t.to_dict()
        self.assertEqual(d["id"], "T-1")
        t2 = Task.from_dict(d)
        self.assertEqual(t2.id, t.id)
        self.assertEqual(t2.state, DONE)
        self.assertEqual(t2.review_round, 2)

    def test_terminal_states(self):
        self.assertIn(DONE, TERMINAL_STATES)

    def test_utcnow_is_iso(self):
        self.assertIn("T", utcnow())


if __name__ == "__main__":
    unittest.main()
