import unittest
from laoban.core.employee import Employee
from laoban.core.permission import (
    can_collaborate, can_use_tool,
    require_collaboration, PermissionDenied,
)


class TestPermission(unittest.TestCase):
    def test_collaboration_allowed(self):
        pm = Employee(id="pm", name="老谋", permissions={"collaboration": ["reviewer"]})
        self.assertTrue(can_collaborate(pm, "reviewer"))

    def test_collaboration_denied(self):
        pm = Employee(id="pm", name="老谋", permissions={"collaboration": ["reviewer"]})
        self.assertFalse(can_collaborate(pm, "dev"))

    def test_require_collaboration_raises(self):
        pm = Employee(id="pm", name="老谋", permissions={"collaboration": []})
        with self.assertRaises(PermissionDenied):
            require_collaboration(pm, "dev")

    def test_tool_allowlist(self):
        dev = Employee(id="dev", name="阿码", capabilities={"tools": ["file_rw"]})
        self.assertTrue(can_use_tool(dev, "file_rw"))
        self.assertFalse(can_use_tool(dev, "shell_exec"))

    def test_employee_roundtrip(self):
        dev = Employee(id="dev", name="阿码", title="开发工程师")
        d = dev.to_dict()
        self.assertEqual(d["name"], "阿码")
        dev2 = Employee.from_dict(d)
        self.assertEqual(dev2.id, dev.id)
        self.assertEqual(dev2.title, "开发工程师")


if __name__ == "__main__":
    unittest.main()
