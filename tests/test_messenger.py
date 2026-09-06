import tempfile
import unittest

from laoban.core.messenger import send, inbox, sent
from laoban.core.employee import Employee
from laoban.core.permission import PermissionDenied, can_message
from laoban.core.store import JsonStore


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(id="pm", name="老谋",
                              permissions={"collaboration": ["dev"]}))
    st.save_employee(Employee(id="dev", name="阿码"))
    st.save_employee(Employee(id="reviewer", name="严审"))
    return st


class TestMessengerPermission(unittest.TestCase):
    def test_empty_collaboration_open(self):
        # 组织内默认开放：collaboration 为空 = 可联系任何人
        dev = Employee(id="dev", name="阿码")
        self.assertTrue(can_message(dev, "pm"))

    def test_whitelist_restricts(self):
        pm = Employee(id="pm", name="老谋",
                      permissions={"collaboration": ["dev"]})
        self.assertTrue(can_message(pm, "dev"))
        self.assertFalse(can_message(pm, "reviewer"))


class TestMessenger(unittest.TestCase):
    def setUp(self):
        self.store = _mk_store()

    def test_send_and_inbox(self):
        msg = send(self.store, "pm", "dev", "请优先处理数据清洗", task_id="T-1")
        self.assertTrue(msg["id"].startswith("MSG-"))
        box = inbox(self.store, "dev")
        self.assertEqual(len(box), 1)
        self.assertEqual(box[0]["content"], "请优先处理数据清洗")
        self.assertEqual(box[0]["task_id"], "T-1")
        # 发件箱
        out = sent(self.store, "pm")
        self.assertEqual(len(out), 1)

    def test_send_denied_by_whitelist(self):
        # pm 白名单只含 dev，向 reviewer 发消息被拒；dev 无白名单 = 默认开放
        with self.assertRaises(PermissionDenied):
            send(self.store, "pm", "reviewer", "越权联系")
        m = send(self.store, "dev", "reviewer", "默认开放可联系")
        self.assertTrue(m["id"].startswith("MSG-"))

    def test_send_to_unknown_employee(self):
        with self.assertRaises(KeyError):
            send(self.store, "pm", "ghost", "hi")

    def test_send_from_terminated(self):
        from laoban.core.lifecycle import terminate_employee
        terminate_employee(self.store, "pm")
        with self.assertRaises(ValueError):
            send(self.store, "pm", "dev", "已解雇不能发消息")

    def test_inbox_order_newest_first(self):
        send(self.store, "pm", "dev", "第一条")
        send(self.store, "pm", "dev", "第二条")
        box = inbox(self.store, "dev")
        self.assertEqual(box[0]["content"], "第二条")


class TestMessengerCli(unittest.TestCase):
    def test_cli_send_and_inbox(self):
        from laoban.cli import main
        with tempfile.TemporaryDirectory() as d:
            root = str(d)
            main(["hire", "--root", root, "--name", "老谋", "--id", "pm"])
            main(["hire", "--root", root, "--name", "阿码", "--id", "dev"])
            self.assertEqual(main(["msg", "send", "--root", root, "--from", "pm",
                                   "--to", "dev", "--content", "在吗"]), 0)
            self.assertEqual(main(["msg", "inbox", "--root", root, "--who", "dev"]), 0)
            box = inbox(JsonStore(root), "dev")
            self.assertEqual(len(box), 1)
            self.assertEqual(box[0]["content"], "在吗")


if __name__ == "__main__":
    unittest.main()
