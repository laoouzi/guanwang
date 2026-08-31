import tempfile
import unittest

from laoban.bootstrap import bootstrap_org, FOUNDERS
from laoban.core.store import JsonStore
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM


def make_gateway():
    gw = LLMGateway()
    for pid in ("hr", "legal", "it", "cfo"):
        gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 组织设计建议"]))
    return gw


class TestBootstrap(unittest.TestCase):
    def test_founders_defined(self):
        self.assertEqual({f["id"] for f in FOUNDERS}, {"hr", "legal", "it", "cfo"})

    def test_bootstrap_creates_founders_and_departments(self):
        root = tempfile.mkdtemp()
        store = JsonStore(root)
        result = bootstrap_org(store, make_gateway(), business="做跨境电商")
        self.assertEqual(len(store.list_employees()), 4)  # 四元老（含财务专家）
        self.assertIn("组织设计方案", result)
        # 四元老用各自 MockLLM 产出建议
        self.assertIn("hr", result)
        self.assertIn("legal", result)
        self.assertIn("it", result)
        self.assertIn("cfo", result)

    def test_founders_have_departments(self):
        root = tempfile.mkdtemp()
        store = JsonStore(root)
        bootstrap_org(store, make_gateway(), business="做跨境电商")
        depts = {e.department for e in store.list_employees()}
        self.assertEqual(depts, {"hr_dept", "legal_dept", "it_dept", "fin_dept"})


if __name__ == "__main__":
    unittest.main()
