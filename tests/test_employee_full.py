import unittest
from laoban.core.employee import Employee


class TestEmployeeFull(unittest.TestCase):
    def test_full_fields_roundtrip(self):
        e = Employee(
            id="emp-dev-001", name="陈默",
            reports_to="emp-pm-001", source="hired",
            job_description={"mission": "交付代码", "duties": [], "workflow_rules": [], "escalation": "转人类"},
            performance_goals={"max_concurrent": 3, "budget_daily_cost": 20.0},
            capabilities={"tools": ["file_rw"], "skills": ["python"], "model_fit": ["tool_loop"]},
            model_config={"provider": "deepseek", "model": "deepseek-chat"},
            permissions={"collaboration": [], "autonomy_level": "supervised"},
            memory={"experiences": [], "notes": []},
            workspace={"dir": "workspaces/emp-dev-001/", "queue": [], "context": {}},
        )
        e2 = Employee.from_dict(e.to_dict())
        self.assertEqual(e2.reports_to, "emp-pm-001")
        self.assertEqual(e2.job_description["mission"], "交付代码")
        self.assertEqual(e2.permissions["autonomy_level"], "supervised")
        self.assertEqual(e2.memory["experiences"], [])
        self.assertEqual(e2.workspace["dir"], "workspaces/emp-dev-001/")

    def test_defaults_still_sane(self):
        e = Employee(id="x", name="y")
        self.assertEqual(e.memory["experiences"], [])
        self.assertEqual(e.permissions["autonomy_level"], "supervised")


if __name__ == "__main__":
    unittest.main()
