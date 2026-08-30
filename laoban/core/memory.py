from __future__ import annotations

from typing import Any

from .employee import Employee


def record_experience(emp: Employee, task_type: str, outcome: str, learned: str) -> None:
    emp.memory["experiences"].append({
        "task_type": task_type, "outcome": outcome, "learned": learned,
    })


def add_note(emp: Employee, text: str) -> None:
    emp.memory["notes"].append(text)


def recall(emp: Employee) -> dict[str, Any]:
    return emp.memory
