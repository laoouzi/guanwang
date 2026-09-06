import unittest
from laoban.core.employee import Employee
from laoban.core.memory import record_experience, add_note, recall


class TestMemory(unittest.TestCase):
    def test_record_experience(self):
        e = Employee(id="dev", name="阿码")
        record_experience(e, task_type="bugfix", outcome="success", learned="先读测试")
        self.assertEqual(len(e.memory["experiences"]), 1)
        self.assertEqual(e.memory["experiences"][0]["task_type"], "bugfix")

    def test_add_note(self):
        e = Employee(id="dev", name="阿码")
        add_note(e, "该客户不要纯管理背景")
        self.assertEqual(e.memory["notes"], ["该客户不要纯管理背景"])

    def test_recall_returns_memory(self):
        e = Employee(id="dev", name="阿码")
        add_note(e, "n1")
        data = recall(e)
        self.assertEqual(data["notes"], ["n1"])
        self.assertEqual(data["experiences"], [])


if __name__ == "__main__":
    unittest.main()
