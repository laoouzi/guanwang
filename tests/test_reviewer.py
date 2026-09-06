import unittest
from laoban.runner.reviewer import Reviewer, ReviewDecision
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM
from laoban.core.employee import Employee
from laoban.core.task import Task


def make_gateway(verdict):
    gw = LLMGateway()
    gw.register_mock("reviewer", MockLLM(responses=[verdict]))
    return gw


class TestReviewer(unittest.TestCase):
    def test_approve(self):
        gw = make_gateway("[准奏] 方案完整，验收标准明确")
        r = Reviewer(gw, checklist=["完整性"])
        decision = r.review(Employee(id="reviewer", name="严审", model_config={"provider": "reviewer"}),
                            Task(id="T-1", title="x"), plan="方案内容")
        self.assertTrue(decision.approved)
        self.assertIn("准奏", decision.reason)

    def test_reject(self):
        gw = make_gateway("[封驳] 缺少性能测试")
        r = Reviewer(gw, checklist=["完整性"])
        decision = r.review(Employee(id="reviewer", name="严审", model_config={"provider": "reviewer"}),
                            Task(id="T-1", title="x"), plan="")
        self.assertFalse(decision.approved)

    def test_default_checklist(self):
        r = Reviewer(make_gateway("x"))
        self.assertTrue(len(r.checklist) >= 3)

    def test_reject_keyword_bohui(self):
        gw = make_gateway("驳回：方案不完整")
        r = Reviewer(gw)
        decision = r.review(Employee(id="reviewer", name="严审", model_config={"provider": "reviewer"}), Task(id="T-1", title="x"), plan="")
        self.assertFalse(decision.approved)


if __name__ == "__main__":
    unittest.main()
