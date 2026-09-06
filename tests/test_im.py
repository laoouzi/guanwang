"""IM 渠道接入测试：绑定层 / 路由 / 飞书适配 / Webhook API。

设计要点（对应 docs 规划的「轻通信」多渠道方案）：
- 消息总线是唯一事实源，IM 只是入口/出口渠道；
- IM 账号 ↔ 员工 id 通过绑定表映射（管理员 CLI 维护）；
- 消息格式约定「同事id: 内容」，未指定且无默认收件人时给出使用提示；
- 收件人是人类时只投递（有绑定则同步推送其 IM），不触发 LLM。
"""
import json
import tempfile
import threading
import unittest
import urllib.request

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.messenger import inbox
from laoban.llm.base import LLMResponse
from laoban.llm.gateway import LLMGateway
from laoban.im.binding import Bindings
from laoban.im.router import parse_target, route_inbound
from laoban.im.feishu import FeishuClient, FeishuWebhook


class RecordingLLM:
    def __init__(self, responses: list[str]):
        self._responses = responses
        self._idx = 0
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        r = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return LLMResponse(content=r)


class FakePush:
    """记录推送的假渠道回调；可注入异常验证容错。"""

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, im_user: str, text: str):
        if self.fail:
            raise RuntimeError("渠道推送失败（模拟）")
        self.sent.append((im_user, text))


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(id="dev", name="阿码", model_config={"provider": "dev"},
                               permissions={"can_assign_human_tasks": True}))
    st.save_employee(Employee(id="emp-chen", name="陈工", kind="human"))
    st.save_employee(Employee(id="emp-xiaoli", name="小李", kind="human"))
    return st


def _mk_gw(store, responses):
    llm = RecordingLLM(responses)
    gw = LLMGateway()
    gw.register_provider("dev", llm)
    return gw, llm


class TestBindings(unittest.TestCase):
    def test_bind_and_lookup(self):
        bd = Bindings(tempfile.mkdtemp())
        item = bd.bind("feishu", "ou_a", "emp-chen")
        self.assertEqual(item["employee"], "emp-chen")
        self.assertEqual(bd.lookup("feishu", "ou_a"), "emp-chen")
        self.assertIsNone(bd.lookup("feishu", "ou_other"))
        self.assertIsNone(bd.lookup("wecom", "ou_a"))  # 平台隔离

    def test_persist_across_instances(self):
        root = tempfile.mkdtemp()
        Bindings(root).bind("feishu", "ou_a", "emp-chen")
        self.assertEqual(Bindings(root).lookup("feishu", "ou_a"), "emp-chen")

    def test_bind_upsert_overwrites(self):
        bd = Bindings(tempfile.mkdtemp())
        bd.bind("feishu", "ou_a", "emp-chen")
        bd.bind("feishu", "ou_a", "emp-xiaoli")
        self.assertEqual(bd.lookup("feishu", "ou_a"), "emp-xiaoli")
        self.assertEqual(len(bd.list()), 1)

    def test_lookup_by_employee(self):
        bd = Bindings(tempfile.mkdtemp())
        bd.bind("feishu", "ou_a", "emp-chen")
        self.assertEqual(bd.lookup_by_employee("feishu", "emp-chen"), "ou_a")
        self.assertIsNone(bd.lookup_by_employee("feishu", "emp-xiaoli"))

    def test_unbind(self):
        bd = Bindings(tempfile.mkdtemp())
        bd.bind("feishu", "ou_a", "emp-chen")
        self.assertTrue(bd.unbind("feishu", "ou_a"))
        self.assertFalse(bd.unbind("feishu", "ou_a"))
        self.assertIsNone(bd.lookup("feishu", "ou_a"))


class TestParseTarget(unittest.TestCase):
    def setUp(self):
        self.st = _mk_store()

    def test_colon_target(self):
        target, content = parse_target("dev: 数据放哪了？", self.st)
        self.assertEqual(target, "dev")
        self.assertEqual(content, "数据放哪了？")

    def test_chinese_colon(self):
        target, content = parse_target("emp-chen：请核对", self.st)
        self.assertEqual(target, "emp-chen")
        self.assertEqual(content, "请核对")

    def test_unknown_token_not_target(self):
        # "9:00 开会" 的 9 不是员工 id → 整条视为内容
        target, content = parse_target("9:00 开会", self.st)
        self.assertIsNone(target)
        self.assertEqual(content, "9:00 开会")

    def test_plain_text_no_target(self):
        target, content = parse_target("大家好", self.st)
        self.assertIsNone(target)
        self.assertEqual(content, "大家好")


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.st = _mk_store()
        self.gw, self.llm = _mk_gw(self.st, ["样本在共享盘 /data/v2 目录。"])
        self.bd = Bindings(tempfile.mkdtemp())
        self.bd.bind("feishu", "ou_a", "emp-chen")

    def test_unbound_sender_gets_hint(self):
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_ghost", "dev: hi", push)
        self.assertEqual(len(push.sent), 1)
        self.assertIn("未绑定", push.sent[0][1])

    def test_human_to_ai_chat_roundtrip(self):
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a",
                      "dev: 数据放哪了？", push)
        # 回信推回提问者的 IM
        self.assertEqual(push.sent, [("ou_a", "样本在共享盘 /data/v2 目录。")])
        # 消息总线双向落库
        self.assertTrue(any("数据放哪了？" in m["content"]
                            for m in inbox(self.st, "dev")))
        self.assertTrue(any("共享盘" in m["content"]
                            for m in inbox(self.st, "emp-chen")))
        self.assertEqual(self.llm.calls, 1)

    def test_human_to_human_relay(self):
        self.bd.bind("feishu", "ou_b", "emp-xiaoli")
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a",
                      "emp-xiaoli: 请复核异常值", push)
        # 不触发 LLM；消息落小李收件箱并推送到其 IM；发送者收到确认
        self.assertEqual(self.llm.calls, 0)
        self.assertTrue(any("请复核异常值" in m["content"]
                            for m in inbox(self.st, "emp-xiaoli")))
        pushed_users = [u for u, _ in push.sent]
        self.assertIn("ou_b", pushed_users)
        relay = [t for u, t in push.sent if u == "ou_b"]
        self.assertIn("请复核异常值", relay[0])
        confirm = [t for u, t in push.sent if u == "ou_a"]
        self.assertIn("已投递", confirm[0])

    def test_human_target_unbound_im(self):
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a",
                      "emp-xiaoli: 请复核", push)
        confirm = push.sent[0][1]
        self.assertIn("已投递", confirm)
        self.assertIn("未绑定 IM", confirm)

    def test_unknown_target_error_pushed(self):
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a", "ghost: hi", push)
        self.assertIn("不存在", push.sent[0][1])

    def test_no_target_no_default_gives_usage(self):
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a", "在吗", push)
        self.assertIn("同事id", push.sent[0][1])

    def test_default_to(self):
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a", "在吗",
                      push, default_to="dev")
        self.assertEqual(push.sent[0][1], "样本在共享盘 /data/v2 目录。")

    def test_permission_denied_error_pushed(self):
        dev = self.st.load_employee("dev")
        dev.permissions["collaboration"] = ["pm"]
        self.st.save_employee(dev)
        push = FakePush()
        route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a",
                      "dev: 越权提问", push)
        self.assertIn("无权", push.sent[0][1])

    def test_push_failure_swallowed(self):
        # 渠道推送失败不应炸掉路由（消息总线仍是事实源）
        push = FakePush(fail=True)
        result = route_inbound(self.st, self.gw, self.bd, "feishu", "ou_a",
                               "dev: 数据放哪了？", push)
        self.assertTrue(any("共享盘" in m["content"]
                            for m in inbox(self.st, "emp-chen")))
        self.assertIn("推送失败", result["summary"])

    def test_gateway_none_ai_target_guard(self):
        push = FakePush()
        route_inbound(self.st, None, self.bd, "feishu", "ou_a",
                      "dev: 在吗", push)
        self.assertIn("网关", push.sent[0][1])

    def test_gateway_none_human_target_still_relays(self):
        self.bd.bind("feishu", "ou_b", "emp-xiaoli")
        push = FakePush()
        route_inbound(self.st, None, self.bd, "feishu", "ou_a",
                      "emp-xiaoli: 请复核", push)
        self.assertIn("ou_b", [u for u, _ in push.sent])


def _msg_event(open_id: str, text: str, event_id: str = "evt-1") -> dict:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1",
                   "token": "tok"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id}},
            "message": {"chat_id": "oc_1", "message_id": "om_1",
                        "message_type": "text",
                        "content": json.dumps({"text": text})},
        },
    }


class TestFeishuWebhook(unittest.TestCase):
    def setUp(self):
        self.st = _mk_store()
        self.gw, self.llm = _mk_gw(self.st, ["FAKE-REPLY-OK"])
        self.bd = Bindings(tempfile.mkdtemp())
        self.bd.bind("feishu", "ou_a", "emp-chen")
        self.client = FakeFeishuClient()
        self.hook = FeishuWebhook(self.st, self.gw, self.client, self.bd,
                                   verification_token="tok")

    def handle(self, body: dict):
        status, payload = self.hook.handle(body, background=False)
        return status, payload

    def test_url_verification(self):
        status, payload = self.handle({"type": "url_verification",
                                       "challenge": "abc123", "token": "tok"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["challenge"], "abc123")

    def test_token_mismatch_403(self):
        body = _msg_event("ou_a", "dev: hi")
        body["header"]["token"] = "wrong"
        status, _ = self.handle(body)
        self.assertEqual(status, 403)

    def test_message_roundtrip(self):
        status, payload = self.handle(_msg_event("ou_a", "dev: 数据放哪了？"))
        self.assertEqual(status, 200)
        self.assertEqual(self.client.sent, [("ou_a", "FAKE-REPLY-OK")])
        self.assertEqual(self.llm.calls, 1)

    def test_duplicate_event_ignored(self):
        body = _msg_event("ou_a", "dev: hi", event_id="evt-dup")
        self.handle(body)
        before = self.llm.calls
        self.handle(body)
        self.assertEqual(self.llm.calls, before)

    def test_non_text_message_notice(self):
        body = _msg_event("ou_a", "")
        body["event"]["message"]["message_type"] = "image"
        status, _ = self.handle(body)
        self.assertEqual(status, 200)
        self.assertEqual(self.llm.calls, 0)
        self.assertIn("文本", self.client.sent[0][1])

    def test_unbound_sender(self):
        self.handle(_msg_event("ou_ghost", "dev: hi"))
        self.assertIn("未绑定", self.client.sent[0][1])

    def test_other_event_type_acked(self):
        body = _msg_event("ou_a", "dev: hi")
        body["header"]["event_type"] = "contact.user.updated_v3"
        status, _ = self.handle(body)
        self.assertEqual(status, 200)
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(self.client.sent, [])


class FakeFeishuClient:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send_text(self, open_id: str, text: str) -> dict:
        self.sent.append((open_id, text))
        return {"code": 0}


class TestDashboardWebhookApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from laoban.dashboard.server import DashboardServer
        cls.store = _mk_store()
        gw, _ = _mk_gw(cls.store, ["API-REPLY-OK"])
        bd = Bindings(tempfile.mkdtemp())
        bd.bind("feishu", "ou_a", "emp-chen")
        cls.hook = FeishuWebhook(cls.store, gw, FakeFeishuClient(), bd)
        cls.server = DashboardServer(cls.store, port=0, gateway=gw,
                                      feishu=cls.hook)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req)

    def test_url_verification_endpoint(self):
        with self._post("/api/im/webhook/feishu",
                        {"type": "url_verification", "challenge": "c1"}) as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read())
        self.assertEqual(data["challenge"], "c1")

    def test_message_endpoint(self):
        import time
        with self._post("/api/im/webhook/feishu",
                        _msg_event("ou_a", "dev: hi", event_id="evt-api")) as r:
            self.assertEqual(r.status, 200)
        # 后台线程异步回信（飞书要求 3 秒内 ACK，回信线程生成）
        for _ in range(40):
            if self.hook.client.sent:
                break
            time.sleep(0.05)
        self.assertEqual(self.hook.client.sent, [("ou_a", "API-REPLY-OK")])

    def test_not_configured_503(self):
        from laoban.dashboard.server import DashboardServer
        import threading
        server = DashboardServer(_mk_store(), port=0, feishu=None)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{server.port}"
        try:
            import urllib.error
            req = urllib.request.Request(
                base + "/api/im/webhook/feishu",
                data=json.dumps({"type": "url_verification"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 503)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
