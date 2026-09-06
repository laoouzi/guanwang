import tempfile
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.task import Task
from laoban.core.messenger import inbox, send as msg_send
from laoban.llm.base import Message, LLMResponse
from laoban.llm.gateway import LLMGateway
from laoban.runner.runner import Runner
from laoban.runner.chat import chat_reply


class RecordingLLM:
    def __init__(self, responses: list[str]):
        self._responses = responses
        self._idx = 0
        self.captured: list[list[Message]] = []

    def chat(self, messages, tools=None):
        self.captured.append(list(messages))
        r = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return LLMResponse(content=r)


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(
        id="dev", name="阿码", model_config={"provider": "dev"},
        permissions={"can_assign_human_tasks": True}))
    st.save_employee(Employee(
        id="emp-chen", name="陈工", kind="human", title="数据核查员"))
    return st


class TestRunnerHearsInbox(unittest.TestCase):
    """D-1：AI 执行任务时能看到收件箱里的同事留言。"""

    def test_inbox_injected_into_system(self):
        st = _mk_store()
        msg_send(st, "emp-chen", "dev", "数据样本已放在共享盘，请注意版本 v2")
        llm = RecordingLLM(["收到"])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        Runner(gw, store=st).run(st.load_employee("dev"), Task(id="T-1", title="x"))
        system = llm.captured[0][0].content
        self.assertIn("同事留言", system)
        self.assertIn("emp-chen", system)
        self.assertIn("数据样本已放在共享盘", system)

    def test_empty_inbox_no_section(self):
        st = _mk_store()
        llm = RecordingLLM(["好"])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        Runner(gw, store=st).run(st.load_employee("dev"), Task(id="T-1", title="x"))
        self.assertNotIn("同事留言", llm.captured[0][0].content)

    def test_inbox_limited_to_recent(self):
        # 只注入最近 5 条，防 token 膨胀
        st = _mk_store()
        for i in range(8):
            msg_send(st, "emp-chen", "dev", f"第{i}条留言")
        llm = RecordingLLM(["好"])
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        Runner(gw, store=st).run(st.load_employee("dev"), Task(id="T-1", title="x"))
        system = llm.captured[0][0].content
        self.assertIn("第7条留言", system)   # 最新
        self.assertNotIn("第1条留言", system)  # 最旧被截掉
        self.assertNotIn("第2条留言", system)


class TestChatReply(unittest.TestCase):
    """D-2：人→AI 提问，AI 回信并落消息总线。"""

    def setUp(self):
        self.store = _mk_store()
        self.llm = RecordingLLM(["样本在共享盘 /data/v2 目录，请查收。"])
        self.gw = LLMGateway()
        self.gw.register_provider("dev", self.llm)

    def test_reply_roundtrip(self):
        result = chat_reply(self.store, self.gw, "emp-chen", "dev", "数据样本放哪了？")
        # 人的问题进了 dev 收件箱
        dev_box = inbox(self.store, "dev")
        self.assertTrue(any(m["content"] == "数据样本放哪了？" for m in dev_box))
        # AI 的回复回了陈工收件箱
        chen_box = inbox(self.store, "emp-chen")
        self.assertTrue(any("共享盘" in m["content"] for m in chen_box))
        self.assertEqual(result["reply"], "样本在共享盘 /data/v2 目录，请查收。")
        # 提问消息带 task 标记（可审计）
        self.assertTrue(result["question"]["id"].startswith("MSG-"))

    def test_reply_context_includes_question(self):
        chat_reply(self.store, self.gw, "emp-chen", "dev", "数据样本放哪了？")
        system = self.llm.captured[0][0].content
        self.assertIn("数据样本放哪了？", system)  # 收件箱注入让 AI 看到提问

    def test_reply_to_human_target_no_llm(self):
        # 收件人是人类：只投递消息，不触发 LLM
        result = chat_reply(self.store, self.gw, "dev", "emp-chen", "请核对数据")
        self.assertIsNone(result["reply"])
        self.assertEqual(len(self.llm.captured), 0)
        self.assertEqual(len(inbox(self.store, "emp-chen")), 1)

    def test_unknown_target(self):
        with self.assertRaises(KeyError):
            chat_reply(self.store, self.gw, "emp-chen", "ghost", "hi")

    def test_permission_denied(self):
        # dev 白名单收紧后，陈工→dev 被拒
        dev = self.store.load_employee("dev")
        dev.permissions["collaboration"] = ["pm"]
        self.store.save_employee(dev)
        from laoban.core.permission import PermissionDenied
        with self.assertRaises(PermissionDenied):
            chat_reply(self.store, self.gw, "emp-chen", "dev", "越权提问")

    def test_suspended_target(self):
        from laoban.core.lifecycle import suspend_employee
        suspend_employee(self.store, "dev")
        with self.assertRaises(ValueError):
            chat_reply(self.store, self.gw, "emp-chen", "dev", "在吗")


class TestChatFailureFallback(unittest.TestCase):
    """D-4：LLM 执行失败也回信——不留「提问已送达却等不到回复」的半失败状态。"""

    def test_llm_failure_replies_fallback(self):
        class _FailingLLM:
            def chat(self, messages, tools=None):
                raise RuntimeError("LLM 服务不可用")

        st = _mk_store()
        gw = LLMGateway()
        gw.register_provider("dev", _FailingLLM())
        result = chat_reply(st, gw, "emp-chen", "dev", "在吗")
        # 提问者收到明确回执（而非异常上抛 / 石沉大海）
        self.assertIn("暂时无法回复", result["reply"])
        chen_box = inbox(st, "emp-chen")
        self.assertTrue(any("暂时无法回复" in m["content"] for m in chen_box))
        # 提问本身也已投递（消息总线一致）
        self.assertTrue(any(m["content"] == "在吗" for m in inbox(st, "dev")))


class TestChatDashboardApi(unittest.TestCase):
    """D-3：看板 POST /api/chat（人从 Web 聊天框与 AI 对话）。"""

    @classmethod
    def setUpClass(cls):
        import threading
        from laoban.dashboard.server import DashboardServer
        cls.store = _mk_store()
        llm = RecordingLLM(["共享盘 /data/v2 目录。"])
        cls.llm = llm
        gw = LLMGateway()
        gw.register_provider("dev", llm)
        cls.server = DashboardServer(cls.store, port=0, gateway=gw)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _post(self, path: str, payload: dict):
        import json as _json
        import urllib.request
        req = urllib.request.Request(
            self.base + path,
            data=_json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req)

    def test_chat_endpoint_roundtrip(self):
        import json as _json
        with self._post("/api/chat", {"from": "emp-chen", "to": "dev",
                                      "content": "数据放哪了？"}) as r:
            self.assertEqual(r.status, 200)
            data = _json.loads(r.read())
        self.assertIn("共享盘", data["reply"])
        # 双向消息落库
        self.assertTrue(any(m["content"] == "数据放哪了？"
                            for m in inbox(self.store, "dev")))
        self.assertTrue(any("共享盘" in m["content"]
                            for m in inbox(self.store, "emp-chen")))

    def test_chat_to_human_delivers_only(self):
        import json as _json
        before = len(self.llm.captured)
        with self._post("/api/chat", {"from": "dev", "to": "emp-chen",
                                      "content": "请核对"}) as r:
            data = _json.loads(r.read())
        self.assertIsNone(data["reply"])
        self.assertEqual(len(self.llm.captured), before)

    def test_chat_unknown_target_404(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/chat", {"from": "emp-chen", "to": "ghost",
                                     "content": "hi"})
        self.assertEqual(ctx.exception.code, 404)

    def test_chat_missing_fields_400(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/chat", {"from": "emp-chen"})
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
