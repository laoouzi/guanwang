import tempfile
import unittest

from laoban.core.directory import render_directory, roster_lines
from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task, ASSIGNED
from laoban.core.workstation import enqueue


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(
        id="dev", name="阿码", title="开发工程师", department="dev_dept",
        capabilities={"tools": ["python_exec", "file_write"]}))
    st.save_employee(Employee(
        id="emp-chen", name="陈工", kind="human", title="数据核查员",
        department="dev_dept"))
    st.save_employee(Employee(id="reviewer", name="严审", title="评审员",
                              department="legal_dept"))
    return st


class TestDirectory(unittest.TestCase):
    def setUp(self):
        self.store = _mk_store()

    def test_roster_covers_all_active_employees(self):
        lines = roster_lines(self.store)
        self.assertEqual(len(lines), 3)

    def test_line_contains_collab_signals(self):
        text = render_directory(self.store)
        # AI 员工：身份/职责/部门/工具能力
        self.assertIn("[AI] dev 阿码", text)
        self.assertIn("开发工程师", text)
        self.assertIn("dev_dept", text)
        self.assertIn("python_exec", text)
        # 人类员工：kind 标注
        self.assertIn("[人类] emp-chen 陈工", text)
        self.assertIn("数据核查员", text)
        # 忙闲：在办任务数
        self.assertIn("在办0", text)

    def test_queue_length_reflected(self):
        task = Task(id="T-1", title="x", state=ASSIGNED)
        self.store.save_task(task)
        enqueue(self.store, "dev", "T-1")
        text = render_directory(self.store)
        self.assertIn("在办1", text)

    def test_terminated_excluded(self):
        from laoban.core.lifecycle import terminate_employee
        terminate_employee(self.store, "reviewer")
        self.assertEqual(len(roster_lines(self.store)), 2)
        self.assertNotIn("reviewer", render_directory(self.store))

    def test_suspended_marked(self):
        from laoban.core.lifecycle import suspend_employee
        suspend_employee(self.store, "reviewer")
        text = render_directory(self.store)
        self.assertIn("reviewer", text)
        self.assertIn("停职", text)

    def test_exclude_self(self):
        text = render_directory(self.store, exclude_id="dev")
        self.assertNotIn("dev 阿码", text)
        self.assertIn("emp-chen", text)

    def test_mission_from_job_description(self):
        emp = self.store.load_employee("dev")
        emp.job_description["mission"] = "按任务要求产出代码与数据交付物"
        self.store.save_employee(emp)
        self.assertIn("按任务要求产出代码", render_directory(self.store))


if __name__ == "__main__":
    unittest.main()
