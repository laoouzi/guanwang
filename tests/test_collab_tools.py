import tempfile
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import ASSIGNED
from laoban.core.messenger import inbox
from laoban.core.workstation import queue_of
from laoban.runner.collab_tools import build_collab_tools


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(
        id="dev", name="阿码", permissions={"can_assign_human_tasks": True}))
    st.save_employee(Employee(
        id="emp-chen", name="陈工", kind="human", title="数据核查员"))
    st.save_employee(Employee(id="dev2", name="阿码二号"))
    st.save_employee(Employee(id="reviewer", name="严审"))
    return st


class TestCollabTools(unittest.TestCase):
    def setUp(self):
        self.store = _mk_store()
        self.actor = self.store.load_employee("dev")
        self.tools = build_collab_tools(self.store, self.actor)

    def test_tool_names(self):
        self.assertEqual(set(self.tools), {"send_message", "delegate_task"})

    def _exec(self, name, args):
        return self.tools[name].execute(args)

    def test_send_message_ok(self):
        out = self._exec("send_message", {"to": "dev2", "content": "帮我复核"})
        self.assertIn("✅", out)
        self.assertEqual(len(inbox(self.store, "dev2")), 1)

    def test_send_message_denied_feedback(self):
        # 拒绝要回给 AI 可读的反馈（而非抛异常炸掉执行）
        self.actor.permissions["collaboration"] = ["dev2"]
        self.store.save_employee(self.actor)
        tools = build_collab_tools(self.store, self.actor)
        out = tools["send_message"].execute({"to": "reviewer", "content": "越权"})
        self.assertIn("❌", out)

    def test_send_message_unknown_target(self):
        out = self._exec("send_message", {"to": "ghost", "content": "hi"})
        self.assertIn("❌", out)
        self.assertIn("不存在", out)

    def test_delegate_to_human(self):
        out = self._exec("delegate_task", {
            "assignee": "emp-chen", "title": "核查异常值",
            "instruction": "核对三份样本数据", "due": "2026-08-30"})
        self.assertIn("✅", out)
        self.assertIn("HT-", out)
        # 落到人类收件箱，created_by 是发起的 AI
        from laoban.core.human_inbox import HumanInbox
        pending = HumanInbox(self.store).list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].created_by, "dev")
        self.assertEqual(pending[0].source, "ai_delegated")

    def test_delegate_to_human_without_permission(self):
        self.actor.permissions["can_assign_human_tasks"] = False
        self.store.save_employee(self.actor)
        tools = build_collab_tools(self.store, self.actor)
        out = tools["delegate_task"].execute({
            "assignee": "emp-chen", "title": "x"})
        self.assertIn("❌", out)
        self.assertIn("can_assign_human_tasks", out)

    def test_delegate_to_ai_creates_task_and_enqueues(self):
        out = self._exec("delegate_task", {
            "assignee": "dev2", "title": "写单元测试",
            "instruction": "用 unittest 补 5 条"})
        self.assertIn("✅", out)
        tasks = self.store.list_tasks()
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.state, ASSIGNED)
        self.assertEqual(task.instruction, "用 unittest 补 5 条")
        self.assertEqual(queue_of(self.store, "dev2"), [task.id])

    def test_delegate_to_suspended_target(self):
        from laoban.core.lifecycle import suspend_employee
        suspend_employee(self.store, "dev2")
        out = self._exec("delegate_task", {"assignee": "dev2", "title": "x"})
        self.assertIn("❌", out)
        self.assertIn("停职", out)

    def test_delegate_unknown_target(self):
        out = self._exec("delegate_task", {"assignee": "ghost", "title": "x"})
        self.assertIn("❌", out)
        self.assertIn("不存在", out)
        self.assertIn("通讯录", out)  # 引导 AI 从通讯录重选

    def test_delegate_requires_title(self):
        out = self._exec("delegate_task", {"assignee": "dev2", "title": ""})
        self.assertIn("❌", out)


if __name__ == "__main__":
    unittest.main()
