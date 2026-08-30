from __future__ import annotations

from .employee import Employee


class PermissionDenied(Exception):
    """越权调用。"""


def can_collaborate(from_emp: Employee, to_emp_id: str) -> bool:
    return to_emp_id in from_emp.permissions.get("collaboration", [])


def require_collaboration(from_emp: Employee, to_emp_id: str) -> None:
    if not can_collaborate(from_emp, to_emp_id):
        raise PermissionDenied(f"{from_emp.id} 无权联系 {to_emp_id}")


def can_use_tool(emp: Employee, tool: str) -> bool:
    return tool in emp.capabilities.get("tools", [])
