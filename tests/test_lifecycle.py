import tempfile
import unittest

from laoban.core.lifecycle import (
    activate_employee, suspend_employee, terminate_employee,
)
from laoban.core.dispatcher import dispatch
from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task, TRIAGE, ASSIGNED


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = JsonStore(self.root)
        self.store.save_employee(Employee(id="dev", name="阿码"))

    def test_suspend_then_activate(self):
        emp = suspend_employee(self.store, "dev")
        self.assertEqual(emp.status, "suspended")
        emp2 = activate_employee(self.store, "dev")
        self.assertEqual(emp2.status, "active")

    def test_terminate_is_terminal(self):
        terminate_employee(self.store, "dev")
        with self.assertRaises(ValueError):
            activate_employee(self.store, "dev")   # 解雇不可复活

    def test_suspend_only_active(self):
        suspend_employee(self.store, "dev")
        with self.assertRaises(ValueError):
            suspend_employee(self.store, "dev")    # 不可重复停职
        terminate_employee(self.store, "dev")
        with self.assertRaises(ValueError):
            terminate_employee(self.store, "dev")  # 不可重复解雇

    def test_unknown_employee(self):
        with self.assertRaises(KeyError):
            suspend_employee(self.store, "nobody")

    def test_dispatch_skips_suspended(self):
        # 派单守卫：非 active 员工不可承接任务
        emp = Employee(id="receptionist", name="小助")
        task = Task(id="T-1", title="x", state=TRIAGE)
        self.assertEqual(dispatch(task, {"receptionist": emp}).id, "receptionist")
        emp.status = "suspended"
        self.assertIsNone(dispatch(task, {"receptionist": emp}))
        emp.status = "terminated"
        self.assertIsNone(dispatch(task, {"receptionist": emp}))


class TestLifecycleCli(unittest.TestCase):
    def test_cli_suspend_activate_terminate(self):
        from laoban.cli import main
        with tempfile.TemporaryDirectory() as d:
            root = str(d)
            main(["hire", "--root", root, "--name", "阿码", "--id", "dev"])
            self.assertEqual(main(["employee", "suspend", "--root", root, "--id", "dev"]), 0)
            self.assertEqual(self.store_status(root, "dev"), "suspended")
            self.assertEqual(main(["employee", "activate", "--root", root, "--id", "dev"]), 0)
            self.assertEqual(self.store_status(root, "dev"), "active")
            self.assertEqual(main(["employee", "terminate", "--root", root, "--id", "dev"]), 0)
            self.assertEqual(self.store_status(root, "dev"), "terminated")
            # 解雇后再上岗 → 命令报错返回非 0
            self.assertNotEqual(main(["employee", "activate", "--root", root, "--id", "dev"]), 0)

    @staticmethod
    def store_status(root, emp_id) -> str:
        return JsonStore(root).load_employee(emp_id).status


if __name__ == "__main__":
    unittest.main()
