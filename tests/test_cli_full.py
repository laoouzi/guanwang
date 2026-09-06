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

    def test_human_to_human_result_return_loop(self):
        """人→人闭环：--from 指定发起人，完成后结果回传，CLI 可查。"""
        from laoban.core.human_inbox import HumanInbox
        from laoban.core.store import JsonStore
        main(["init", "--root", self.root])
        main(["hire", "--root", self.root, "--name", "陈工", "--kind", "human"])
        main(["hire", "--root", self.root, "--name", "小李", "--kind", "human"])
        # 陈工派活给小李
        rc = main(["todo", "add", "--root", self.root, "--assignee", "emp-小李",
                   "--title", "复核异常值清单", "--source", "self",
                   "--from", "emp-陈工"])
        self.assertEqual(rc, 0)
        inbox = HumanInbox(JsonStore(self.root))
        ht = inbox.list_pending()[0]
        self.assertEqual(ht.created_by, "emp-陈工")
        # 小李完成后，结果回传陈工
        main(["todo", "done", "--root", self.root, "--id", ht.id,
              "--result", "12 条异常全部人工复核确认"])
        rc = main(["todo", "results", "--root", self.root, "--who", "emp-陈工"])
        self.assertEqual(rc, 0)
        rs = inbox.results_for("emp-陈工")
        self.assertEqual(len(rs), 1)
        self.assertIn("12 条", rs[0].result)
        # 小李自己没有回传（活是他干的，结果是回给发起人的）
        self.assertEqual(inbox.results_for("emp-小李"), [])

    def test_today_shows_returned_results_hint(self):
        # today 清单页会提示有回传结果待查看
        from laoban.core.human_inbox import HumanInbox
        from laoban.core.store import JsonStore
        main(["init", "--root", self.root])
        main(["hire", "--root", self.root, "--name", "陈工", "--kind", "human"])
        main(["todo", "add", "--root", self.root, "--assignee", "emp-陈工",
              "--title", "配合核查", "--from", "boss"])
        inbox = HumanInbox(JsonStore(self.root))
        ht = inbox.list_pending()[0]
        main(["todo", "done", "--root", self.root, "--id", ht.id, "--result", "done"])
        # today 无待办但有回传 → rc=0 且有结果提示路径
        rc = main(["today", "--root", self.root, "--who", "emp-陈工"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
