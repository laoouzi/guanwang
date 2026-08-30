from __future__ import annotations

from typing import Any

from .task import Task, TRIAGE, PLANNING, REVIEW, ASSIGNED, REPORTING
from .employee import Employee

# 状态 → 默认负责岗位 ID（与启动模式/默认模板约定的岗位 id 对齐）
_STATE_AGENT_MAP = {
    TRIAGE: "receptionist",      # 前台助理
    PLANNING: "pm",              # 项目经理
    REVIEW: "reviewer",          # 评审员
    ASSIGNED: "pm",              # 派发由 PM 执行
    REPORTING: "pm",             # 汇总由 PM 执行
}


def resolve_agent_for_state(state: str) -> str | None:
    return _STATE_AGENT_MAP.get(state)


def dispatch(task: Task, employees: dict[str, Employee]) -> Employee | None:
    """根据任务状态解析目标员工。Doing/Next 由 org 推断，v0.1 简化：按状态映射。"""
    agent_id = resolve_agent_for_state(task.state)
    if agent_id is None:
        return None
    return employees.get(agent_id)
