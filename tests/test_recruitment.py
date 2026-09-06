import tempfile
import unittest

from laoban.recruitment import submit_headcount_request, approve_headcount, get_request
from laoban.core.store import JsonStore
from laoban.core.employee import Employee


class TestRecruitment(unittest.TestCase):
    def setUp(self):
        root = tempfile.mkdtemp()
        self.store = JsonStore(root)
        self.store.save_employee(Employee(id="pm", name="老谋", department="dev_dept"))

    def test_submit_and_approve_new_ai(self):
        req = submit_headcount_request(self.store, requester="pm", reason="业务量增加",
                                       headcount=1, role="开发工程师", cost=3.0)
        self.assertEqual(req["status"], "pending")
        # 审批通过后员工入职
        approve_headcount(self.store, req["id"], approver="boss")
        emps = self.store.list_employees()
        self.assertEqual(len(emps), 2)  # pm + 新入职员工
        new = [e for e in emps if e.id != "pm"][0]
        self.assertEqual(new.kind, "ai")
        self.assertEqual(new.title, "开发工程师")
        # 申请单状态流转
        self.assertEqual(get_request(self.store, req["id"])["status"], "approved")

    def test_submit_hire_human(self):
        # 轨道 B 也支持招聘人类员工（入部门树，kind=human）
        req = submit_headcount_request(self.store, requester="pm", reason="需要人类面试官",
                                       headcount=1, role="技术面试官", cost=8.0,
                                       hire_type="hire_human", department="dev_dept")
        approve_headcount(self.store, req["id"], approver="boss")
        new = [e for e in self.store.list_employees() if e.id != "pm"][0]
        self.assertEqual(new.kind, "human")
        self.assertEqual(new.department, "dev_dept")

    def test_clone_ai(self):
        self.store.save_employee(Employee(id="dev-001", name="阿码", title="开发工程师",
                                          department="dev_dept"))
        req = submit_headcount_request(self.store, requester="pm", reason="工作量翻倍",
                                       headcount=1, role="开发工程师", cost=3.0,
                                       hire_type="clone_ai", source_emp_id="dev-001")
        approve_headcount(self.store, req["id"], approver="boss")
        clones = [e for e in self.store.list_employees()
                  if e.id not in ("pm", "dev-001")]
        self.assertEqual(len(clones), 1)
        self.assertEqual(clones[0].title, "开发工程师")  # 继承档案
        self.assertEqual(clones[0].source, "cloned")
        self.assertNotEqual(clones[0].id, "dev-001")    # 预算独立（新员工新档案）

    def test_submit_requires_reason(self):
        with self.assertRaises(ValueError):
            submit_headcount_request(self.store, requester="pm", reason="", headcount=1)

    def test_double_approve_rejected(self):
        req = submit_headcount_request(self.store, requester="pm", reason="业务量增加", headcount=1)
        approve_headcount(self.store, req["id"], approver="boss")
        with self.assertRaises(ValueError):
            approve_headcount(self.store, req["id"], approver="boss")


if __name__ == "__main__":
    unittest.main()
