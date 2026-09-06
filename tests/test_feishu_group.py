"""飞书群聊 @提及 与 加密事件 测试。

群聊：
- chat_type=group 且 @了机器人（mentions 非空）→ 处理，回信推回群（chat_id）；
- 群里没 @机器人 → 忽略（不吵群）；
- 提及 token（@_user_1）从文本剥离后再解析「同事id: 内容」；
- 人→人中转仍走对方 DM 绑定，不打扰群。

加密事件（可选依赖 pycryptodome / cryptography，缺失则跳过）：
- body={"encrypt": ...} → AES-256-CBC（key=SHA256(encrypt_key)）解出 JSON 后照常处理；
- 未配 encrypt_key 收到加密体 → 400 明确报错。
"""
import json
import tempfile
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.core.messenger import inbox
from laoban.im.binding import Bindings
from laoban.im.feishu import FeishuWebhook
from laoban.llm.base import LLMResponse
from laoban.llm.gateway import LLMGateway

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    _HAS_CRYPTO = True
except ImportError:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding as _cp
        _HAS_CRYPTO = True
    except ImportError:
        _HAS_CRYPTO = False


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(id="dev", name="阿码", model_config={"provider": "dev"}))
    st.save_employee(Employee(id="emp-chen", name="陈工", kind="human"))
    st.save_employee(Employee(id="emp-xiaoli", name="小李", kind="human"))
    return st


class FakeFeishuClient:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []  # (接收者, 文本) —— DM 与群共用

    def send_text(self, open_id: str, text: str) -> dict:
        self.sent.append((open_id, text))
        return {"code": 0}

    def send_text_chat(self, chat_id: str, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"code": 0}


def _mk_hook(store, gw, bindings=None, encrypt_key="", token=""):
    client = FakeFeishuClient()
    bd = bindings or Bindings(tempfile.mkdtemp())
    bd.bind("feishu", "ou_a", "emp-chen")
    hook = FeishuWebhook(store, gw, client, bd,
                         verification_token=token, encrypt_key=encrypt_key)
    return hook, client


def _event(text: str, open_id="ou_a", chat_type="p2p",
           mentions=None, event_id="evt-1", chat_id="oc_1") -> dict:
    ev = {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id}},
            "message": {"chat_id": chat_id, "message_id": "om_1",
                        "chat_type": chat_type, "message_type": "text",
                        "content": json.dumps({"text": text})},
        },
    }
    if mentions is not None:
        ev["event"]["message"]["mentions"] = mentions
    return ev


def _mention_bot(key="@_user_1"):
    return [{"key": key, "id": {"open_id": "ou_bot"}, "name": "老板助手"}]


class RecordingLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        r = self._responses[self._i % len(self._responses)]
        self._i += 1
        return LLMResponse(content=r)


def _mk_gw(responses):
    llm = RecordingLLM(responses)
    gw = LLMGateway()
    gw.register_provider("dev", llm)
    return gw, llm


class TestGroupChat(unittest.TestCase):
    def setUp(self):
        self.st = _mk_store()
        self.gw, self.llm = _mk_gw(["群聊回信 GROUP-REPLY-OK。"])
        self.hook, self.client = _mk_hook(self.st, self.gw)

    def test_group_mention_routes_and_replies_to_chat(self):
        status, _ = self.hook.handle(
            _event("@_user_1 dev: 数据放哪了？", chat_type="group",
                   mentions=_mention_bot(), event_id="evt-g1"),
            background=False)
        self.assertEqual(status, 200)
        self.assertEqual(self.llm.calls, 1)
        # 回信进群（chat_id），而不是 DM 提问者
        self.assertEqual(self.client.sent, [("oc_1", "群聊回信 GROUP-REPLY-OK。")])
        self.assertTrue(any("数据放哪了？" in m["content"]
                            for m in inbox(self.st, "dev")))

    def test_group_without_mention_ignored(self):
        status, _ = self.hook.handle(
            _event("dev: 数据放哪了？", chat_type="group", event_id="evt-g2"),
            background=False)
        self.assertEqual(status, 200)
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(self.client.sent, [])

    def test_mention_token_stripped_before_parse(self):
        # @机器人后紧跟内容（无「同事id:」前缀）→ default_to 生效或提示
        self.hook.default_to = "dev"
        self.hook.handle(_event("@_user_1 数据放哪了？", chat_type="group",
                                mentions=_mention_bot(), event_id="evt-g3"),
                         background=False)
        self.assertEqual(self.llm.calls, 1)

    def test_group_human_to_human_relay_goes_dm(self):
        self.hook.bindings.bind("feishu", "ou_b", "emp-xiaoli")
        self.hook.handle(
            _event("@_user_1 emp-xiaoli: 请复核", chat_type="group",
                   mentions=_mention_bot(), event_id="evt-g4"),
            background=False)
        # 中转走小李的 DM；确认/回执进群
        receivers = [r for r, _ in self.client.sent]
        self.assertIn("ou_b", receivers)   # 小李 DM 收到中转
        self.assertIn("oc_1", receivers)   # 群里收到回执
        dm_text = [t for r, t in self.client.sent if r == "ou_b"][0]
        self.assertIn("请复核", dm_text)

    def test_p2p_unchanged(self):
        # 私聊不带 @ 也照常处理（原行为）
        self.hook.handle(_event("dev: 数据放哪了？", event_id="evt-p1"),
                         background=False)
        self.assertEqual(self.llm.calls, 1)
        self.assertEqual(self.client.sent[0][0], "ou_a")  # DM 回提问者


def _encrypt(payload: dict, encrypt_key: str) -> str:
    import base64
    import hashlib
    key = hashlib.sha256(encrypt_key.encode()).digest()
    data = json.dumps(payload, ensure_ascii=False).encode()
    try:
        cipher = AES.new(key, AES.MODE_CBC, b"\x00" * 16)
        ct = cipher.encrypt(pad(data, 16))
    except NameError:
        p = _cp.PKCS7(128).padder()
        ct = (Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16))
              .encryptor()).update(p.update(data) + p.finalize())
    return base64.b64encode(ct).decode()


@unittest.skipUnless(_HAS_CRYPTO, "需要 pycryptodome 或 cryptography")
class TestEncryptedEvents(unittest.TestCase):
    ENCRYPT_KEY = "feishu-encrypt-key-1"

    def setUp(self):
        self.st = _mk_store()
        self.gw, self.llm = _mk_gw(["加密回信 ENC-REPLY-OK。"])
        self.hook, self.client = _mk_hook(self.st, self.gw,
                                           encrypt_key=self.ENCRYPT_KEY)

    def test_encrypted_message_roundtrip(self):
        body = {"encrypt": _encrypt(
            _event("dev: 数据放哪了？", event_id="evt-e1"), self.ENCRYPT_KEY)}
        status, payload = self.hook.handle(body, background=False)
        self.assertEqual(status, 200)
        self.assertEqual(self.llm.calls, 1)
        self.assertEqual(self.client.sent, [("ou_a", "加密回信 ENC-REPLY-OK。")])

    def test_encrypted_url_verification(self):
        plain = {"type": "url_verification", "challenge": "enc-ch-42"}
        body = {"encrypt": _encrypt(plain, self.ENCRYPT_KEY)}
        status, payload = self.hook.handle(body, background=False)
        self.assertEqual(status, 200)
        self.assertEqual(payload["challenge"], "enc-ch-42")

    def test_wrong_encrypt_key_fails(self):
        body = {"encrypt": _encrypt(
            _event("dev: hi", event_id="evt-e2"), "another-key")}
        status, payload = self.hook.handle(body, background=False)
        self.assertEqual(status, 400)
        self.assertIn("解密失败", payload.get("error", ""))

    def test_encrypted_without_key_configured(self):
        hook, _ = _mk_hook(self.st, self.gw, encrypt_key="")
        body = {"encrypt": _encrypt(
            _event("dev: hi", event_id="evt-e3"), "k")}
        status, payload = hook.handle(body, background=False)
        self.assertEqual(status, 400)
        self.assertIn("encrypt_key", payload.get("error", ""))


if __name__ == "__main__":
    unittest.main()
