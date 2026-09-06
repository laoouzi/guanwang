import unittest
import time
from laoban.core.task import Task, PENDING, TRIAGE
from laoban.core.employee import Employee
from laoban.core.permission import PermissionDenied
from laoban.core.dispatcher import dispatch, resolve_agent_for_state
from laoban.core.scheduler import check_stall


class TestDispatcher(unittest.TestCase):
    def test_resolve_agent_for_state(self):
        self.assertEqual(resolve_agent_for_state(TRIAGE), "receptionist")

    def test_dispatch_returns_target(self):
        emp = Employee(id="receptionist", name="小助")
        task = Task(id="T-1", title="x", state=TRIAGE)
        target = dispatch(task, {"receptionist": emp})
        self.assertEqual(target.id, "receptionist")

    def test_dispatch_unknown_state(self):
        task = Task(id="T-1", title="x", state=PENDING)
        self.assertIsNone(dispatch(task, {}))


class TestScheduler(unittest.TestCase):
    def test_no_stall_when_fresh(self):
        task = Task(id="T-1", title="x", state=TRIAGE)
        self.assertFalse(check_stall(task, threshold_sec=180))

    def test_stall_detected(self):
        task = Task(id="T-1", title="x", state=TRIAGE)
        task.updated_at = time.time() - 200
        self.assertTrue(check_stall(task, threshold_sec=180))


if __name__ == "__main__":
    unittest.main()
