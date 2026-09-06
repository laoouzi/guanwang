import tempfile
import unittest
from pathlib import Path

from laoban.core.store import JsonStore
from laoban.core.task import Task, DOING
from laoban.core.employee import Employee


class TestJsonStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = JsonStore(self.tmp)

    def test_task_roundtrip(self):
        t = Task(id="T-1", title="x", state=DOING)
        self.store.save_task(t)
        loaded = self.store.load_task("T-1")
        self.assertEqual(loaded.id, "T-1")
        self.assertEqual(loaded.state, DOING)

    def test_list_tasks(self):
        self.store.save_task(Task(id="T-1", title="a"))
        self.store.save_task(Task(id="T-2", title="b"))
        self.assertEqual(len(self.store.list_tasks()), 2)

    def test_employee_roundtrip(self):
        e = Employee(id="dev", name="阿码")
        self.store.save_employee(e)
        loaded = self.store.load_employee("dev")
        self.assertEqual(loaded.name, "阿码")

    def test_missing_task_is_none(self):
        self.assertIsNone(self.store.load_task("nope"))

    def test_no_leftover_tmp_files(self):
        self.store.save_task(Task(id="T-1", title="x"))
        leftovers = list(Path(self.tmp, "tasks").glob("*.tmp"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
