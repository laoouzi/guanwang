"""员工鉴权测试：口令库 / CLI / 看板会话（登录-身份强制-登出）。

设计：
- 口令 PBKDF2-HMAC-SHA256 存储（盐 + 迭代次数落盘），常量时间比较；
- 看板登录换会话 Cookie（HttpOnly）；一旦任何员工设过口令，聊天必须
  登录且只能以自身身份发送（401/403）；未设任何口令 = 本地免鉴权模式
  （保持原行为，向后兼容）。
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from laoban.core.auth import AuthStore
from laoban.core.employee import Employee
from laoban.core.store import JsonStore
from laoban.cli import main
from laoban.llm.base import LLMResponse
from laoban.llm.gateway import LLMGateway


class RecordingLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0

    def chat(self, messages, tools=None):
        r = self._responses[self._i % len(self._responses)]
        self._i += 1
        return LLMResponse(content=r)


def _mk_store():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(id="dev", name="阿码", model_config={"provider": "dev"}))
    st.save_employee(Employee(id="emp-chen", name="陈工", kind="human"))
    return st


def _mk_gw():
    llm = RecordingLLM(["回信内容 OK。"])
    gw = LLMGateway()
    gw.register_provider("dev", llm)
    return gw, llm


class TestAuthStore(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.auth = AuthStore(self.root)

    def test_set_and_verify(self):
        self.auth.set_password("emp-chen", "s3cret")
        self.assertTrue(self.auth.verify("emp-chen", "s3cret"))
        self.assertFalse(self.auth.verify("emp-chen", "wrong"))
        self.assertFalse(self.auth.verify("emp-chen", ""))

    def test_unknown_employee_no_password(self):
        self.assertFalse(self.auth.verify("ghost", "x"))

    def test_not_enabled_initially(self):
        self.assertFalse(self.auth.enabled())
        self.auth.set_password("emp-chen", "pw")
        self.assertTrue(self.auth.enabled())

    def test_overwrite_password(self):
        self.auth.set_password("emp-chen", "old")
        self.auth.set_password("emp-chen", "new")
        self.assertFalse(self.auth.verify("emp-chen", "old"))
        self.assertTrue(self.auth.verify("emp-chen", "new"))

    def test_persist_across_instances(self):
        AuthStore(self.root).set_password("emp-chen", "pw")
        self.assertTrue(AuthStore(self.root).verify("emp-chen", "pw"))

    def test_salt_unique_per_user(self):
        a = AuthStore(tempfile.mkdtemp())
        a.set_password("emp-chen", "same")
        b = AuthStore(tempfile.mkdtemp())
        b.set_password("emp-chen", "same")
        self.assertNotEqual(a._load()["employees"]["emp-chen"]["salt"],
                            b._load()["employees"]["emp-chen"]["salt"])

    def test_list_accounts(self):
        self.auth.set_password("emp-chen", "pw")
        self.assertEqual(self.auth.list_accounts(), ["emp-chen"])

    def test_remove_password(self):
        self.auth.set_password("emp-chen", "pw")
        self.assertTrue(self.auth.remove("emp-chen"))
        self.assertFalse(self.auth.remove("emp-chen"))
        self.assertFalse(self.auth.verify("emp-chen", "pw"))


class TestAuthCli(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        _mk_store() if False else None
        st = JsonStore(self.root)
        st.save_employee(Employee(id="emp-chen", name="陈工", kind="human"))

    def test_passwd_and_list(self):
        rc = main(["auth", "passwd", "--root", self.root,
                   "--who", "emp-chen", "--password", "pw123"])
        self.assertEqual(rc, 0)
        self.assertTrue(AuthStore(self.root).verify("emp-chen", "pw123"))
        rc = main(["auth", "list", "--root", self.root])
        self.assertEqual(rc, 0)

    def test_passwd_unknown_employee(self):
        rc = main(["auth", "passwd", "--root", self.root,
                   "--who", "ghost", "--password", "x"])
        self.assertEqual(rc, 1)

    def test_passwd_remove(self):
        main(["auth", "passwd", "--root", self.root,
              "--who", "emp-chen", "--password", "pw"])
        rc = main(["auth", "remove", "--root", self.root, "--who", "emp-chen"])
        self.assertEqual(rc, 0)
        self.assertFalse(AuthStore(self.root).enabled())


class _SessionClient:
    """带 Cookie 的极简 HTTP 客户端（http.cookiejar 也可以，这里手收 Set-Cookie）。"""

    def __init__(self, base):
        self.base = base
        self.cookie = ""

    def request(self, path, payload=None, method=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers,
            method=method or ("POST" if data else "GET"))
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            set_cookie = resp.headers.get("Set-Cookie", "")
            if set_cookie:
                self.cookie = set_cookie.split(";")[0]
            return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class TestDashboardSession(unittest.TestCase):
    """鉴权启用后：登录换 Cookie，聊天只能以自身身份发送。"""

    @classmethod
    def setUpClass(cls):
        from laoban.dashboard.server import DashboardServer
        cls.store = _mk_store()
        cls.auth = AuthStore(cls.store.root)
        cls.auth.set_password("emp-chen", "pw-chen")
        gw, cls.llm = _mk_gw()
        cls.server = DashboardServer(cls.store, port=0, gateway=gw, auth=cls.auth)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _SessionClient(f"http://127.0.0.1:{cls.server.port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_login_wrong_password(self):
        c = _SessionClient(self.client.base)
        status, body = c.request("/api/login", {"id": "emp-chen", "password": "bad"})
        self.assertEqual(status, 401)

    def test_login_logout_flow(self):
        status, body = self.client.request(
            "/api/login", {"id": "emp-chen", "password": "pw-chen"})
        self.assertEqual(status, 200)
        self.assertEqual(body["id"], "emp-chen")
        self.assertTrue(self.client.cookie)  # 会话 Cookie 已下发
        status, me = self.client.request("/api/me")
        self.assertEqual(status, 200)
        self.assertEqual(me["id"], "emp-chen")
        self.assertEqual(me["kind"], "human")
        # 以自身身份聊天 → 通过
        status, body = self.client.request("/api/chat", {
            "from": "emp-chen", "to": "dev", "content": "在吗"})
        self.assertEqual(status, 200)
        self.assertIn("回信内容", body["reply"])
        # 伪造他人身份 → 403
        status, body = self.client.request("/api/chat", {
            "from": "emp-xiaoli", "to": "dev", "content": "冒充"})
        self.assertEqual(status, 403)
        # 登出后 → 401
        status, _ = self.client.request("/api/logout", {})
        self.assertEqual(status, 200)
        status, _ = self.client.request("/api/me")
        self.assertEqual(status, 401)
        status, _ = self.client.request("/api/chat", {
            "from": "emp-chen", "to": "dev", "content": "x"})
        self.assertEqual(status, 401)

    def test_chat_without_session(self):
        c = _SessionClient(self.client.base)
        status, body = c.request("/api/chat", {
            "from": "emp-chen", "to": "dev", "content": "hi"})
        self.assertEqual(status, 401)

    def test_login_unknown_employee(self):
        c = _SessionClient(self.client.base)
        status, _ = c.request("/api/login", {"id": "ghost", "password": "x"})
        self.assertEqual(status, 404)


class TestDashboardNoAuthBackCompat(unittest.TestCase):
    """未设任何口令 = 本地免鉴权模式（原行为不变）。"""

    def test_chat_without_login_still_works(self):
        from laoban.dashboard.server import DashboardServer
        store = _mk_store()
        gw, _ = _mk_gw()
        server = DashboardServer(store, port=0, gateway=gw,
                                 auth=AuthStore(store.root))
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        c = _SessionClient(f"http://127.0.0.1:{server.port}")
        try:
            status, body = c.request("/api/chat", {
                "from": "emp-chen", "to": "dev", "content": "免鉴权"})
            self.assertEqual(status, 200)
            self.assertIn("回信内容", body["reply"])
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
