from __future__ import annotations

from .employee import Employee
from .memory import record_experience


def write_back_experience(emp: Employee, task_type: str, score: int, comment: str = "") -> None:
    """人类验收评分（1-5）→ 结构化回写员工记忆（经验回写最简版）。"""
    outcome = "success" if score >= 3 else "failure"
    record_experience(emp, task_type=task_type, outcome=outcome, learned=comment or "")
