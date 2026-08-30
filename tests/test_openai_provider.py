"""OpenAI 兼容 Provider 测试：本地起假端点，CI 无真实 Key 也能全绿。"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from laoban.llm.base import Message


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """记录请求并返回标准 OpenAI chat/completions 响应。"""

    last_request: dict = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization", "")
        _FakeOpenAIHandler.last_request = {
            "path": self.path, "auth": auth, "body": body,
        }
        if "/error" in self.path:
            payload = {"error": {"message": "boom"}}
            code = 500
        else:
            payload = {
                "id": "chatcmpl-1", "object": "chat.completion",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "你好，我是测试回复"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 9, "completion_tokens": 7, "total_tokens": 16},
            }
            code = 200
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TestOpenAICompatibleProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}/v1"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _provider(self):
        from laoban.llm.openai_compatible import OpenAICompatibleProvider
        return OpenAICompatibleProvider(
            base_url=self.base, api_key="sk-test-123", model="deepseek-chat",
        )

    def test_chat_returns_content(self):
        resp = self._provider().chat([Message(role="user", content="你好")])
        self.assertEqual(resp.content, "你好，我是测试回复")
        self.assertEqual(resp.tool_calls, [])

    def test_request_shape(self):
        self._provider().chat([
            Message(role="system", content="你是员工"),
            Message(role="user", content="干活"),
        ])
        req = _FakeOpenAIHandler.last_request
        # Bearer 鉴权
        self.assertEqual(req["auth"], "Bearer sk-test-123")
        # 路径是 /v1/chat/completions
        self.assertTrue(req["path"].endswith("/chat/completions"))
        # body 带 model 与 messages
        self.assertEqual(req["body"]["model"], "deepseek-chat")
        self.assertEqual(req["body"]["messages"][0]["role"], "system")
        self.assertEqual(req["body"]["messages"][1]["content"], "干活")

    def test_http_error_raises(self):
        from laoban.llm.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(
            base_url=self.base + "/error", api_key="k", model="m")
        with self.assertRaises(Exception):
            p.chat([Message(role="user", content="x")])

    def test_gateway_routes_to_openai_provider(self):
        from laoban.llm.gateway import LLMGateway
        from laoban.llm.openai_compatible import OpenAICompatibleProvider
        gw = LLMGateway()
        gw.register_provider("deepseek", self._provider())
        resp = gw.chat("deepseek", [Message(role="user", content="hi")])
        self.assertEqual(resp.content, "你好，我是测试回复")
        # chat_for_employee 按 model_config.provider 路由
        emp_cfg = {"provider": "deepseek", "model": "deepseek-chat"}
        resp2 = gw.chat_for_employee(emp_cfg, [Message(role="user", content="hi")])
        self.assertEqual(resp2.content, "你好，我是测试回复")


class TestRegisterFromEnv(unittest.TestCase):
    """环境变量自动发现：配了哪个 Key 就注册哪个 provider。"""

    def test_env_registers_providers(self):
        import os
        from laoban.llm.gateway import LLMGateway
        from laoban.llm.openai_compatible import register_from_env

        saved = {}
        env = {
            "LAOBAN_DEEPSEEK_API_KEY": "sk-ds",
            "LAOBAN_DASHSCOPE_API_KEY": "sk-qwen",
            "LAOBAN_OPENAI_API_KEY": "sk-oai",
            "LAOBAN_OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
        }
        for k, v in env.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            gw = LLMGateway()
            registered = register_from_env(gw)
            self.assertEqual(set(registered), {"deepseek", "qwen", "openai", "ollama"})
            for name in registered:
                self.assertIn(name, gw._providers)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_no_env_no_providers(self):
        import os
        from laoban.llm.gateway import LLMGateway
        from laoban.llm.openai_compatible import register_from_env

        keys = ["LAOBAN_DEEPSEEK_API_KEY", "LAOBAN_DASHSCOPE_API_KEY",
                "LAOBAN_OPENAI_API_KEY", "LAOBAN_OLLAMA_BASE_URL"]
        saved = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        try:
            gw = LLMGateway()
            self.assertEqual(register_from_env(gw), [])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_deepseek_default_base_url(self):
        from laoban.llm.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(base_url="", api_key="k", model="m",
                                     default_base="https://api.deepseek.com/v1")
        self.assertEqual(p.base_url, "https://api.deepseek.com/v1")


if __name__ == "__main__":
    unittest.main()
