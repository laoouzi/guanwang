import tempfile
import unittest

from laoban.core.store import JsonStore
from laoban.core.human_inbox import HumanInbox, HumanTask


class TestHumanInbox(unittest.TestCase):
    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        self.inbox = HumanInbox(self.store)

    def test_create_and_list_pending(self):
        self.inbox.create(task_id="T-1", title="核查简历项目贡献", assignee="陈工")
        pending = self.inbox.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].assignee, "陈工")

    def test_complete_removes_from_pending(self):
        self.inbox.create(task_id="T-1", title="核查", assignee="陈工")
        ht = self.inbox.list_pending()[0]
        self.inbox.complete(ht.id, result="核查通过")
        self.assertEqual(len(self.inbox.list_pending()), 0)

    def test_human_task_roundtrip(self):
        ht = HumanTask(id="HT-1", task_id="T-1", title="x", assignee="y")
        ht2 = HumanTask.from_dict(ht.to_dict())
        self.assertEqual(ht2.id, "HT-1")
        self.assertEqual(ht2.status, "pending")


class TestDailyList(unittest.TestCase):
    """人类员工每日任务清单：按人按天过滤（含 AI 派发的配合任务）。"""

    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        self.inbox = HumanInbox(self.store)

    def test_my_tasks_appear_in_my_daily_list(self):
        self.inbox.create(task_id="T-1", title="配合 AI 核查数据", assignee="陈工", due_date="2026-08-30")
        mine = self.inbox.daily_list(assignee="陈工", date="2026-08-30")
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].title, "配合 AI 核查数据")
        self.assertEqual(mine[0].source, "ai_delegated")  # 默认来自 AI 派发

    def test_other_assignees_not_in_my_list(self):
        self.inbox.create(task_id="T-1", title="小李的活", assignee="小李")
        mine = self.inbox.daily_list(assignee="陈工", date="2026-08-30")
        self.assertEqual(mine, [])

    def test_future_due_not_today(self):
        self.inbox.create(task_id="T-1", title="下周才做", assignee="陈工", due_date="2026-09-06")
        mine = self.inbox.daily_list(assignee="陈工", date="2026-08-30")
        self.assertEqual(mine, [])

    def test_overdue_still_on_today_list(self):
        # 逾期未完成的仍出现在今天的清单（继续处理）
        self.inbox.create(task_id="T-1", title="昨天的活", assignee="陈工", due_date="2026-08-29")
        mine = self.inbox.daily_list(assignee="陈工", date="2026-08-30")
        self.assertEqual(len(mine), 1)

    def test_no_due_date_always_on_list(self):
        # 无截止日期的任务随时可见
        self.inbox.create(task_id="T-1", title="不限期", assignee="陈工")
        mine = self.inbox.daily_list(assignee="陈工", date="2026-08-30")
        self.assertEqual(len(mine), 1)

    def test_completed_not_on_list(self):
        ht = self.inbox.create(task_id="T-1", title="已完成", assignee="陈工", due_date="2026-08-30")
        self.inbox.complete(ht.id, result="done")
        mine = self.inbox.daily_list(assignee="陈工", date="2026-08-30")
        self.assertEqual(mine, [])

    def test_self_created_task_with_source(self):
        self.inbox.create(task_id="T-2", title="自查部署脚本", assignee="陈工",
                          due_date="2026-08-30", source="self")
        mine = self.inbox.daily_list(assignee="陈工", date="2026-08-30")
        self.assertEqual(mine[0].source, "self")


if __name__ == "__main__":
    unittest.main()
