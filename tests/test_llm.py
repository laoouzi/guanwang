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


if __name__ == "__main__":
    unittest.main()
