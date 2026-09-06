"""组织通讯录：AI 员工可看见的协作对象花名册。

每行一位员工，压缩呈现路由决策所需信号：
  身份（AI/人类）· id · 姓名 · 职务 · 部门 · 职责 · 能力 · 忙闲 · 状态
解雇（terminated）员工不出现；停职（suspended）标注不可派。
"""
from __future__ import annotations

from .store import JsonStore
from .employee import Employee


def _one_line(emp: Employee) -> str | None:
    if emp.status == "terminated":
        return None
    kind = "人类" if emp.kind == "human" else "AI"
    parts = [f"[{kind}] {emp.id} {emp.name}"]
    if emp.title:
        parts.append(emp.title)
    if emp.department:
        parts.append(emp.department)
    mission = emp.job_description.get("mission", "")
    if mission:
        parts.append(mission)
    tools = emp.capabilities.get("tools", [])
    if tools:
        parts.append(f"工具:{','.join(tools)}")
    if emp.kind == "human":
        skills = emp.capabilities.get("skills", [])
        if skills:
            parts.append(f"技能:{','.join(skills)}")
    parts.append(f"在办{len(emp.workspace.get('queue', []))}")
    if emp.status == "suspended":
        parts.append("停职（暂不可派）")
    return " · ".join(parts)


def roster_lines(store: JsonStore) -> list[str]:
    lines = []
    for emp in store.list_employees():
        line = _one_line(emp)
        if line:
            lines.append(line)
    return lines


def render_directory(store: JsonStore, exclude_id: str = "") -> str:
    lines = []
    for emp in store.list_employees():
        if emp.id == exclude_id:
            continue
        line = _one_line(emp)
        if line:
            lines.append(line)
    return "\n".join(lines)
