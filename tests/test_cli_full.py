import tempfile
import unittest
from pathlib import Path

from laoban.cli import main


class TestCliFull(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_hire_and_list(self):
        main(["init", "--root", self.root])
        rc = main(["hire", "--root", self.root, "--name", "阿码", "--title", "开发工程师"])
        self.assertEqual(rc, 0)
        self.assertTrue((Path(self.root) / "employees" / "emp-阿码.json").exists())

    def test_hire_human_kind(self):
        # 人类员工也入部门树，kind=human 标识
        main(["init", "--root", self.root])
        rc = main(["hire", "--root", self.root, "--name", "陈工", "--kind", "human",
                   "--title", "技术面试官", "--department", "dev_dept"])
        self.assertEqual(rc, 0)
        p = Path(self.root) / "employees" / "emp-陈工.json"
        self.assertTrue(p.exists())
        import json
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d["kind"], "human")
        self.assertEqual(d["department"], "dev_dept")

    def test_task_submit_and_status(self):
        main(["init", "--root", self.root])
        main(["task", "submit", "--root", self.root, "--title", "写函数"])
        main(["task", "status", "--root", self.root])

    def test_today_lists_human_tasks(self):
        # 人类员工每日任务清单（含 AI 派发配合任务）
        main(["init", "--root", self.root])
        main(["hire", "--root", self.root, "--name", "陈工", "--kind", "human"])
        main(["todo", "add", "--root", self.root, "--assignee", "emp-陈工",
              "--title", "配合 AI 核查数据", "--due", "2026-08-30"])
        rc = main(["today", "--root", self.root, "--who", "emp-陈工", "--date", "2026-08-30"])
        self.assertEqual(rc, 0)

    def test_unknown_command_returns_nonzero(self):
        self.assertNotEqual(main(["nonsense"]), 0)


if __name__ == "__main__":
    unittest.main()
