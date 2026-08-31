import unittest
from laoban.llm.base import Message, LLMResponse, LLMProvider
from laoban.llm.mock import MockLLM


class TestMockLLM(unittest.TestCase):
    def test_scripted_response(self):
        llm = MockLLM(responses=["你好，我是开发工程师"])
        resp = llm.chat([Message(role="user", content="hi")])
        self.assertEqual(resp.content, "你好，我是开发工程师")

    def test_round_robin_responses(self):
        llm = MockLLM(responses=["a", "b", "c"])
        self.assertEqual(llm.chat([]).content, "a")
        self.assertEqual(llm.chat([]).content, "b")
        self.assertEqual(llm.chat([]).content, "c")

    def test_exhausted_falls_back(self):
        llm = MockLLM(responses=["only"])
        llm.chat([])
        self.assertEqual(llm.chat([]).content, "only")  # 用完循环回第一条

    def test_default_response(self):
        llm = MockLLM()
        self.assertIsInstance(llm.chat([]).content, str)
        self.assertTrue(len(llm.chat([]).content) > 0)


from laoban.llm.gateway import LLMGateway


class TestLLMGateway(unittest.TestCase):
    def test_route_to_mock(self):
        gw = LLMGateway()
        gw.register_mock("mock", MockLLM(responses=["hi"]))
        resp = gw.chat("mock", [Message(role="user", content="x")])
        self.assertEqual(resp.content, "hi")

    def test_unknown_provider_raises(self):
        gw = LLMGateway()
        with self.assertRaises(KeyError):
            gw.chat("nope", [])

    def test_model_config_resolves_provider(self):
        gw = LLMGateway()
        gw.register_mock("deepseek", MockLLM(responses=["ds"]))
        resp = gw.chat_for_employee({"provider": "deepseek", "model": "deepseek-chat"}, [])
        self.assertEqual(resp.content, "ds")


if __name__ == "__main__":
    unittest.main()
