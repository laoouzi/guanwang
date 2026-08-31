"""v0.2 组织配置化：org.json（部门/岗位/权限模板）→ Employee 实例。

配置查找顺序（resolve_org_path）：
  1. 显式 --file 路径
  2. {root}/org.json（用户定制）
  3. 内置默认模板 laoban/templates/default_org.json

角色（role）字段与 Employee 的映射：
  id/name/kind/title/reports_to → 同名字段
  model                         → model_config（合并到默认值）
  job_description / performance_goals / capabilities / permissions → 同名合并
  founder: true                 → 启动模式创始人（bootstrap 只入职这些人）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core.employee import Employee
from .core.store import JsonStore

DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "default_org.json"

_ROLE_KEY_MAP = {"model_config": "model"}
_MERGE_FIELDS = ("job_description", "performance_goals", "capabilities",
                 "model_config", "compensation", "permissions")


def load_org(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_TEMPLATE
    if not p.exists():
        raise FileNotFoundError(f"组织配置不存在：{p}")
    org = json.loads(p.read_text(encoding="utf-8"))
    validate_org(org)
    return org


def validate_org(org: dict[str, Any]) -> None:
    if not isinstance(org, dict) or not isinstance(org.get("departments"), list) \
            or not org["departments"]:
        raise ValueError("org.json 顶层必须有非空的 departments 数组")
    seen_depts: set[str] = set()
    seen_roles: set[str] = set()
    for dept in org["departments"]:
        if not isinstance(dept, dict) or not dept.get("id"):
            raise ValueError("每个部门必须提供非空 id")
        if dept["id"] in seen_depts:
            raise ValueError(f"部门 id 重复：{dept['id']}")
        seen_depts.add(dept["id"])
        for role in dept.get("roles", []):
            if not isinstance(role, dict) or not role.get("id") or not role.get("name"):
                raise ValueError(f"岗位必须提供非空 id 和 name（部门 {dept['id']}）")
            if role["id"] in seen_roles:
                raise ValueError(f"岗位 id 重复：{role['id']}")
            seen_roles.add(role["id"])
            if role.get("kind") not in (None, "ai", "human"):
                raise ValueError(f"岗位 {role['id']} 的 kind 只能是 ai/human")


def org_file_for_root(root: str | Path) -> Path:
    return Path(root) / "org.json"


def resolve_org_path(file: str | Path | None = None,
                     root: str | Path | None = None) -> Path:
    if file:
        return Path(file)
    if root:
        p = org_file_for_root(root)
        if p.exists():
            return p
    return DEFAULT_TEMPLATE


def load_org_for_store(store: JsonStore) -> dict[str, Any]:
    """按 store 数据目录解析组织配置：用户 org.json 优先，否则默认模板。"""
    return load_org(resolve_org_path(root=store.root))


def build_employee(dept: dict[str, Any], role: dict[str, Any]) -> Employee:
    emp = Employee(id=role["id"], name=role["name"], kind=role.get("kind", "ai"))
    emp.title = role.get("title", "")
    emp.department = dept["id"]
    emp.reports_to = role.get("reports_to", "")
    emp.hired_at = role.get("hired_at") or _now_iso()
    for f in _MERGE_FIELDS:
        v = role.get(_ROLE_KEY_MAP.get(f, f))
        if isinstance(v, dict):
            merged = dict(getattr(emp, f))
            merged.update(v)
            setattr(emp, f, merged)
    return emp


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def instantiate(store: JsonStore, org: dict[str, Any],
                which: str = "all", source: str = "template") -> list[Employee]:
    """按配置实例化员工并入库。

    which：all=全部 / founders=仅创始人 / team=非创始人。
    """
    if which not in ("all", "founders", "team"):
        raise ValueError(f"which 必须是 all/founders/team，收到：{which}")
    created: list[Employee] = []
    for dept in org["departments"]:
        for role in dept.get("roles", []):
            is_founder = bool(role.get("founder"))
            if which == "founders" and not is_founder:
                continue
            if which == "team" and is_founder:
                continue
            emp = build_employee(dept, role)
            emp.source = "founder" if is_founder else source
            emp.workspace["dir"] = f"workspaces/{emp.id}/"
            store.save_employee(emp)
            created.append(emp)
    return created


def iter_roles(org: dict[str, Any]):
    for dept in org["departments"]:
        for role in dept.get("roles", []):
            yield dept, role


def find_role(org: dict[str, Any], role_ref: str) -> tuple[dict, dict] | None:
    """按岗位 id 或 title 精确匹配。"""
    if not role_ref:
        return None
    for dept, role in iter_roles(org):
        if role["id"] == role_ref or role.get("title") == role_ref:
            return dept, role
    return None


def summary(org: dict[str, Any]) -> str:
    lines = [f"公司：{org.get('company', '')}（业务：{org.get('business', '')}）"]
    for dept in org["departments"]:
        roles = dept.get("roles", [])
        lines.append(f"  {dept['id']} · {dept.get('name', '')}（{len(roles)} 个岗位）")
        for role in roles:
            tags = []
            if role.get("founder"):
                tags.append("创始人")
            tags.append("人类" if role.get("kind") == "human" else "AI")
            m = role.get("model", {})
            if m.get("provider"):
                tags.append(f"{m['provider']}:{m.get('model', '')}")
            perm = role.get("permissions", {})
            if perm.get("can_assign_human_tasks"):
                tags.append("可派人类任务")
            if perm.get("spending_limit_per_task") is not None:
                tags.append(f"限额${perm['spending_limit_per_task']}")
            lines.append(f"    - {role['id']} {role['name']} · {role.get('title', '')}"
                         f"（{' / '.join(tags)}）")
    return "\n".join(lines)


def init_config(dest: str | Path, force: bool = False) -> Path:
    p = Path(dest)
    if p.exists() and not force:
        raise FileExistsError(f"组织配置已存在：{p}（--force 可覆盖）")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DEFAULT_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return p
