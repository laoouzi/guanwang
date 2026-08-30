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


if __name__ == "__main__":
    unittest.main()
