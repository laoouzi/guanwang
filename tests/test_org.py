import json
import tempfile
import unittest
from pathlib import Path

from laoban.org import (
    DEFAULT_TEMPLATE, build_employee, find_role, init_config, instantiate,
    iter_roles, load_org, resolve_org_path, summary, validate_org,
)
from laoban.core.store import JsonStore
from laoban.recruitment import submit_headcount_request, approve_headcount


def _write_org(path: Path, org: dict) -> Path:
    path.write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    return path


MINIMAL_ORG = {
    "company": "测试公司",
    "business": "测试业务",
    "departments": [
        {
            "id": "hr_dept", "name": "人资部",
            "roles": [{"id": "hr", "name": "HR", "title": "组织设计", "founder": True}],
        },
        {
            "id": "dev_dept", "name": "研发部",
            "roles": [
                {"id": "dev", "name": "阿码", "title": "开发工程师",
                 "model": {"provider": "deepseek", "model": "deepseek-chat"},
                 "permissions": {"spending_limit_per_task": 9.0,
                                 "can_assign_human_tasks": True},
                 "capabilities": {"tools": ["python_exec"]}},
                {"id": "emp-chen", "name": "陈工", "kind": "human",
                 "title": "数据核查员"},
            ],
        },
    ],
}


class TestLoadOrg(unittest.TestCase):
    def test_default_template_valid(self):
        org = load_org()
        self.assertTrue(org["departments"])
        ids = {r["id"] for _, r in iter_roles(org)}
        self.assertIn("hr", ids)
        self.assertIn("dev", ids)

    def test_load_custom_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_org(Path(d) / "org.json", MINIMAL_ORG)
            org = load_org(p)
            self.assertEqual(org["company"], "测试公司")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_org("/nonexistent/org.json")

    def test_validate_errors(self):
        with self.assertRaises(ValueError):
            validate_org({})
        with self.assertRaises(ValueError):
            validate_org({"departments": []})
        with self.assertRaises(ValueError):
            validate_org({"departments": [{"id": "a", "roles": []},
                                          {"id": "a", "roles": []}]})  # 部门重复
        with self.assertRaises(ValueError):
            validate_org({"departments": [{"id": "a", "roles": [{"id": "x"}]}]})  # 缺 name
        with self.assertRaises(ValueError):
            validate_org({"departments": [
                {"id": "a", "roles": [{"id": "x", "name": "X"}, {"id": "x", "name": "Y"}]}]})
        with self.assertRaises(ValueError):
            validate_org({"departments": [
                {"id": "a", "roles": [{"id": "x", "name": "X", "kind": "robot"}]}]})

    def test_resolve_org_path_prefers_root_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(resolve_org_path(root=root), DEFAULT_TEMPLATE)
            _write_org(root / "org.json", MINIMAL_ORG)
            self.assertEqual(resolve_org_path(root=root), root / "org.json")
            self.assertEqual(resolve_org_path(file=str(root / "org.json")),
                             root / "org.json")


class TestInstantiate(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = JsonStore(self.root)

    def test_instantiate_all(self):
        org = load_org()  # 默认模板（内含 founder 与普通角色）
        emps = instantiate(self.store, org, which="all")
        self.assertEqual(len(emps), len(list(iter_roles(org))))
        by_id = {e.id: e for e in emps}
        self.assertEqual(by_id["hr"].source, "founder")
        self.assertEqual(by_id["dev"].source, "template")
        self.assertEqual(by_id["dev"].workspace["dir"], "workspaces/dev/")

    def test_instantiate_founders_only(self):
        org = load_org()
        emps = instantiate(self.store, org, which="founders")
        ids = {e.id for e in emps}
        self.assertEqual(ids, {"hr", "legal", "it", "cfo"})

    def test_instantiate_team_only(self):
        org = load_org()
        emps = instantiate(self.store, org, which="team")
        ids = {e.id for e in emps}
        self.assertIn("dev", ids)
        self.assertIn("emp-chen", ids)
        self.assertNotIn("hr", ids)
        self.assertNotIn("emp-emp", ids)

    def test_build_employee_merges_template_fields(self):
        dept = MINIMAL_ORG["departments"][1]
        role = dept["roles"][0]  # dev
        emp = build_employee(dept, role)
        self.assertEqual(emp.model_config["provider"], "deepseek")
        self.assertEqual(emp.model_config["model"], "deepseek-chat")
        self.assertEqual(emp.permissions["spending_limit_per_task"], 9.0)
        self.assertTrue(emp.permissions["can_assign_human_tasks"])
        self.assertEqual(emp.capabilities["tools"], ["python_exec"])
        # 默认字段仍在（合并而非替换）
        self.assertEqual(emp.performance_goals["max_concurrent"], 3)

    def test_find_role_by_id_and_title(self):
        org = load_org()
        hit = find_role(org, "dev")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1]["name"], "阿码")
        hit2 = find_role(org, "开发工程师")
        self.assertEqual(hit2[1]["id"], "dev")
        self.assertIsNone(find_role(org, "不存在"))
        self.assertIsNone(find_role(org, ""))

    def test_invalid_which(self):
        with self.assertRaises(ValueError):
            instantiate(self.store, load_org(), which="bogus")


class TestInitConfig(unittest.TestCase):
    def test_init_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".laoban" / "org.json"
            out = init_config(p)
            self.assertTrue(out.exists())
            org = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("departments", org)
            with self.assertRaises(FileExistsError):
                init_config(p)
            init_config(p, force=True)  # 覆盖成功

    def test_summary_renders(self):
        text = summary(load_org())
        self.assertIn("公司：", text)
        self.assertIn("dev_dept", text)
        self.assertIn("dev 阿码", text)


class TestRecruitmentTemplate(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = JsonStore(self.root)

    def test_hire_applies_role_template(self):
        # role 命中模板 id → 套用模型/权限，id/name 用新档案
        req = submit_headcount_request(self.store, requester="pm", reason="扩编",
                                       headcount=1, role="dev", cost=3.0)
        emp = approve_headcount(self.store, req["id"], approver="boss")
        self.assertEqual(emp.title, "开发工程师")
        self.assertEqual(emp.model_config["provider"], "dev")
        self.assertEqual(emp.department, "dev_dept")
        self.assertTrue(emp.id.startswith("emp-"))

    def test_hire_without_template_falls_back(self):
        req = submit_headcount_request(self.store, requester="pm", reason="扩编",
                                       headcount=1, role="神秘岗位", cost=3.0)
        emp = approve_headcount(self.store, req["id"], approver="boss")
        self.assertEqual(emp.title, "神秘岗位")
        self.assertEqual(emp.model_config["provider"], "mock")  # 默认值兜底

    def test_hire_human_with_ai_template_forced_human(self):
        req = submit_headcount_request(self.store, requester="pm", reason="人手不足",
                                       headcount=1, role="dev", cost=8.0,
                                       hire_type="hire_human")
        emp = approve_headcount(self.store, req["id"], approver="boss")
        self.assertEqual(emp.kind, "human")


class TestCliOrg(unittest.TestCase):
    def test_cli_org_init_show_load(self):
        from laoban.cli import main
        with tempfile.TemporaryDirectory() as d:
            root = str(Path(d) / ".laoban")
            self.assertEqual(main(["org", "init-config", "--root", root]), 0)
            self.assertTrue((Path(root) / "org.json").exists())
            # 重复生成拒绝
            self.assertEqual(main(["org", "init-config", "--root", root]), 1)
            # show 走用户配置
            self.assertEqual(main(["org", "show", "--root", root]), 0)
            # load 全量入库
            self.assertEqual(main(["org", "load", "--root", root]), 0)
            emps = JsonStore(root).list_employees()
            ids = {e.id for e in emps}
            self.assertIn("hr", ids)
            self.assertIn("dev", ids)
            self.assertIn("emp-chen", ids)

    def test_cli_org_load_founders_only(self):
        from laoban.cli import main
        with tempfile.TemporaryDirectory() as d:
            root = str(Path(d) / ".laoban")
            self.assertEqual(main(["org", "load", "--root", root,
                                   "--founders-only"]), 0)
            ids = {e.id for e in JsonStore(root).list_employees()}
            self.assertEqual(ids, {"hr", "legal", "it", "cfo"})


if __name__ == "__main__":
    unittest.main()
