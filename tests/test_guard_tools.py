import unittest
from laoban.runner.guard import Guard, classify_risk
from laoban.runner.tools import TOOLS, Tool


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.guard = Guard(blocklist=["rm -rf"], domain_allowlist=["example.com"])

    def test_dangerous_command_high_risk(self):
        self.assertEqual(self.guard.check_command("rm -rf /"), "high")

    def test_safe_command_medium(self):
        self.assertEqual(self.guard.check_command("ls -la"), "high")  # 命令执行一律 high

    def test_domain_allowlist_medium(self):
        self.assertEqual(self.guard.check_url("https://example.com/x"), "medium")

    def test_domain_not_allowlisted_high(self):
        self.assertEqual(self.guard.check_url("https://evil.com/x"), "high")

    def test_file_inside_workspace_low(self):
        self.assertEqual(classify_risk("file_rw", {"path": "workspaces/a/out.txt"}), "low")

    def test_file_outside_workspace_high(self):
        self.assertEqual(classify_risk("file_rw", {"path": "/etc/passwd"}), "high")


class TestTools(unittest.TestCase):
    def test_tool_registered(self):
        self.assertIn("file_rw", TOOLS)
        self.assertIn("shell_exec", TOOLS)
        self.assertIn("web_search", TOOLS)

    def test_tool_has_required_attrs(self):
        t = TOOLS["shell_exec"]
        self.assertIsInstance(t, Tool)
        self.assertTrue(t.name)
        self.assertTrue(t.description)


if __name__ == "__main__":
    unittest.main()
