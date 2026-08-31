import tempfile
import unittest

from laoban.acceptance import run_acceptance, DEV_TASK, DOC_TASK, DATA_TASK
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM


def _gateway():
    gw = LLMGateway()
    for pid in ("receptionist", "pm", "reviewer", "worker"):
        gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 验收产出", "[准奏] OK"]))
    return gw


class TestAcceptanceSuite(unittest.TestCase):
    def test_dev_passed(self):
        result = run_acceptance(_gateway(), suite=(DEV_TASK,),
                                root_dir=tempfile.mkdtemp())[0]
        self.assertEqual(result["category"], "dev")
        self.assertTrue(result["passed"], msg=result["reason"])
        self.assertIsInstance(result["review_passed"], bool)

    def test_doc_requires_four_sections(self):
        result = run_acceptance(_gateway(), suite=(DOC_TASK,),
                                root_dir=tempfile.mkdtemp())[0]
        self.assertTrue(result["passed"], msg=result["reason"])

    def test_data_matches_csv(self):
        result = run_acceptance(_gateway(), suite=(DATA_TASK,),
                                root_dir=tempfile.mkdtemp())[0]
        self.assertTrue(result["passed"], msg=result["reason"])

    def test_full_suite_at_least_2_of_3(self):
        # D2 北极星：真实 LLM 下完成率 ≥ 2/3。MockLLM + 兜底生成应 3/3
        results = run_acceptance(_gateway(), root_dir=tempfile.mkdtemp())
        passed = sum(1 for r in results if r["passed"])
        self.assertGreaterEqual(passed, 2)


if __name__ == "__main__":
    unittest.main()
