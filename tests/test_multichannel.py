"""多 IM 渠道 + 组织同步 + Web Push 单元测试（#26/#27/#28）。

- #26：IMChannel 抽象 / ChannelHub 多渠道路由 / 企微钉钉骨架桩 / SenderChannel
- #27：OrgSyncSource 抽象 / sync_org 增量融合 / FeishuOrgSync 桩
- #28：WebPushManager（VAPID 密钥 / 订阅 / aes128gcm 加解密往返）
"""
from __future__ import annotations

import base64
import os
import struct
import tempfile
import unittest

from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.im.binding import Bindings
from laoban.im.channel import IMChannel, SenderChannel
from laoban.im.hub import ChannelHub, build_hub
from laoban.im.wecom import WeComChannel, wecom_from_env
from laoban.im.dingtalk import DingTalkChannel, dingtalk_from_env
from laoban.im.orgsync import OrgSyncSource, FeishuOrgSync, sync_org


def _mk_store():
    st = JsonStore(tempfile.mkdtemp())
    st.save_employee(Employee(id="boss", name="老板", kind="human"))
    st.save_employee(Employee(id="emp-chen", name="陈工", kind="human"))
    st.save_employee(Employee(id="dev", name="阿码", kind="ai"))
    return st


class _FakeClient:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_text(self, im_user, text):
        if self.fail:
            raise RuntimeError("推送失败（模拟）")
        self.sent.append((im_user, text))


class TestChannelHub(unittest.TestCase):
    """#26 渠道中枢：注册 / 路由 / 多渠道出站。"""

    def test_register_and_platforms(self):
        hub = ChannelHub()
        hub.register(SenderChannel("feishu", _FakeClient()))
        hub.register(SenderChannel("wecom", _FakeClient()))
        self.assertEqual(sorted(hub.platforms()), ["feishu", "wecom"])
        self.assertIsNone(hub.get("dingtalk"))

    def test_handle_unknown_platform(self):
        hub = ChannelHub()
        status, payload = hub.handle("feishu", {})
        self.assertEqual(status, 404)
        self.assertIn("未知 IM 平台", payload["error"])

    def test_push_employee_multichannel(self):
        st = _mk_store()
        bd = Bindings(st.root)
        bd.bind("feishu", "ou-chen", "emp-chen")
        bd.bind("wecom", "wc-chen", "emp-chen")
        feishu = _FakeClient()
        wecom = _FakeClient()
        hub = build_hub(feishu=feishu, wecom=wecom, bindings=bd)
        self.assertTrue(hub.push_employee("emp-chen", "催一下"))
        self.assertEqual(feishu.sent, [("ou-chen", "催一下")])
        self.assertEqual(wecom.sent, [("wc-chen", "催一下")])

    def test_push_employee_unbound(self):
        st = _mk_store()
        hub = build_hub(feishu=_FakeClient(), bindings=Bindings(st.root))
        self.assertFalse(hub.push_employee("emp-chen", "没绑定"))

    def test_push_to(self):
        hub = build_hub(feishu=_FakeClient())
        self.assertTrue(hub.push_to("feishu", "ou-x", "直推"))
        self.assertFalse(hub.push_to("wecom", "x", "无此渠道"))


class TestSenderChannel(unittest.TestCase):
    def test_swallows_exception(self):
        ch = SenderChannel("feishu", _FakeClient(fail=True))
        self.assertFalse(ch.send_text("ou-x", "hi"))   # 异常吞掉返回 False

    def test_handle_501(self):
        ch = SenderChannel("feishu", _FakeClient())
        status, _ = ch.handle({})
        self.assertEqual(status, 501)


class TestStubChannels(unittest.TestCase):
    """企微/钉钉骨架桩：保留接口、默认未接入、安全降级。"""

    def test_wecom_unconfigured(self):
        ch = WeComChannel()
        self.assertFalse(ch.configured)
        self.assertFalse(ch.send_text("wc-x", "hi"))
        self.assertEqual(ch.handle({})[0], 501)
        self.assertEqual(ch.fetch_departments(), [])
        self.assertEqual(ch.fetch_users(), [])

    def test_dingtalk_unconfigured(self):
        ch = DingTalkChannel()
        self.assertFalse(ch.configured)
        self.assertFalse(ch.send_text("dt-x", "hi"))
        self.assertEqual(ch.handle({})[0], 501)
        self.assertEqual(ch.fetch_departments(), [])

    def test_from_env_none(self):
        self.assertIsNone(wecom_from_env())
        self.assertIsNone(dingtalk_from_env())

    def test_from_env_configured(self):
        os.environ["LAOBAN_WECOM_CORP_ID"] = "corp"
        os.environ["LAOBAN_WECOM_AGENT_ID"] = "100"
        os.environ["LAOBAN_WECOM_SECRET"] = "sec"
        try:
            ch = wecom_from_env()
            self.assertIsNotNone(ch)
            self.assertTrue(ch.configured)
        finally:
            for k in ("LAOBAN_WECOM_CORP_ID", "LAOBAN_WECOM_AGENT_ID",
                      "LAOBAN_WECOM_SECRET"):
                os.environ.pop(k, None)


class StubOrgSync(OrgSyncSource):
    platform = "feishu"

    def __init__(self, depts=None, members=None):
        self.depts = depts or []
        self.members = members or []

    def fetch_departments(self):
        return self.depts

    def fetch_members(self):
        return self.members


class TestOrgSync(unittest.TestCase):
    """#27 组织同步：增量融合 + 绑定自动写入 + 不新建（默认）。"""

    def test_sync_matches_by_binding_and_updates(self):
        st = _mk_store()
        bd = Bindings(st.root)
        bd.bind("feishu", "ou-chen", "emp-chen")
        src = StubOrgSync(
            depts=[{"id": "d1", "name": "研发", "parent_id": ""}],
            members=[{"id": "ou-chen", "name": "陈工",
                      "department_ids": ["d1"], "manager_id": "ou-boss"}],
        )
        # 上级也先绑定，验证汇报链解析
        bd.bind("feishu", "ou-boss", "boss")
        r = sync_org(st, src, bd)
        self.assertEqual(r["bound"], 1)
        self.assertEqual(st.load_employee("emp-chen").department, "研发")
        self.assertEqual(st.load_employee("emp-chen").reports_to, "boss")

    def test_sync_does_not_create_by_default(self):
        st = _mk_store()
        bd = Bindings(st.root)
        src = StubOrgSync(members=[{"id": "ou-new", "name": "新人", "department_ids": []}])
        r = sync_org(st, src, bd)
        self.assertEqual(r["created"], 0)
        self.assertEqual(r["bound"], 0)
        self.assertIsNone(st.load_employee("feishu-ou-new"))

    def test_sync_create_flag(self):
        st = _mk_store()
        bd = Bindings(st.root)
        src = StubOrgSync(members=[{"id": "ou-new", "name": "新人", "department_ids": []}])
        r = sync_org(st, src, bd, sync_create=True)
        self.assertEqual(r["created"], 1)
        emp = st.load_employee("feishu-ou-new")
        self.assertIsNotNone(emp)
        self.assertEqual(emp.kind, "human")
        self.assertEqual(bd.lookup("feishu", "ou-new"), "feishu-ou-new")

    def test_sync_never_touches_ai(self):
        st = _mk_store()
        bd = Bindings(st.root)
        # IM 账号被误绑到 AI 员工：同步不得覆盖 AI（部门/汇报链都不动）
        bd.bind("feishu", "ou-dev", "dev")
        src = StubOrgSync(
            depts=[{"id": "d1", "name": "研发", "parent_id": ""}],
            members=[{"id": "ou-dev", "name": "阿码", "department_ids": ["d1"]}],
        )
        r = sync_org(st, src, bd)
        self.assertEqual(r["bound"], 0)
        dev = st.load_employee("dev")
        self.assertEqual(dev.kind, "ai")
        self.assertEqual(dev.department, "")   # AI 部门未被同步覆盖

    def test_feishu_stub_empty(self):
        self.assertEqual(FeishuOrgSync().fetch_departments(), [])
        self.assertEqual(FeishuOrgSync().fetch_members(), [])


class TestWebPush(unittest.TestCase):
    """#28 Web Push：VAPID 密钥 / 订阅 / 加解密往返（对齐 RFC 8188）。"""

    def _crypto(self):
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            from cryptography.hazmat.primitives.serialization import (Encoding,
                PublicFormat)
            return (hashes, ec, AESGCM, HKDF, Encoding, PublicFormat)
        except ImportError:
            return None

    def test_vapid_generated_and_persisted(self):
        from laoban.dashboard.webpush import WebPushManager
        st = _mk_store()
        m = WebPushManager(st)
        self.assertTrue(m.public_key)
        # 重建实例读到同一把公钥（长期复用，客户端订阅不失效）
        m2 = WebPushManager(st)
        self.assertEqual(m.public_key, m2.public_key)

    def test_subscribe_unsubscribe(self):
        from laoban.dashboard.webpush import WebPushManager
        st = _mk_store()
        m = WebPushManager(st)
        m.subscribe("emp-chen", "https://push.example/1", "p", "a")
        m.subscribe("emp-chen", "https://push.example/2", "p", "a")
        self.assertEqual(len(m.subscriptions("emp-chen")), 2)
        self.assertTrue(m.unsubscribe("emp-chen", "https://push.example/1"))
        self.assertEqual(len(m.subscriptions("emp-chen")), 1)
        self.assertFalse(m.unsubscribe("emp-chen", "https://push.example/1"))

    def test_encrypt_roundtrip(self):
        c = self._crypto()
        if c is None:
            self.skipTest("需要 cryptography")
        hashes, ec, AESGCM, HKDF, Encoding, PublicFormat = c
        from laoban.dashboard.webpush import WebPushManager

        def b64u(d):
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        client = ec.generate_private_key(ec.SECP256R1())
        client_pub = client.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        auth = os.urandom(16)

        st = _mk_store()
        m = WebPushManager(st)
        m.subscribe("emp-chen", "https://push.example/1",
                    b64u(client_pub), b64u(auth))
        payload = b'{"title":"x","body":"y"}'
        enc = m._encrypt_aes128gcm(m.subscriptions("emp-chen")[0], payload)

        # 按 RFC 8188 解析并解密验证
        salt = enc[:16]
        idlen = enc[20]
        keyid = enc[21:21 + idlen]          # 临时公钥（65 字节）
        content = enc[21 + idlen:]
        context = b"WebPush: info\x00" + client_pub + keyid
        ecdh = client.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), keyid))
        secret = HKDF(algorithm=hashes.SHA256(), length=32,
                      salt=auth, info=context).derive(ecdh)
        cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
                   info=b"Content-Encoding: aes128gcm\x00").derive(secret)
        nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                     info=b"Content-Encoding: nonce\x00").derive(secret)
        pt = AESGCM(cek).decrypt(nonce, content, None)
        self.assertEqual(pt, payload + b"\x02")   # 末记录分隔符


if __name__ == "__main__":
    unittest.main()
