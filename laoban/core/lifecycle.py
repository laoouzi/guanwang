from __future__ import annotations

from .employee import Employee
from .store import JsonStore

# 解雇不可逆：terminated 是终态，activate 不可复活
ACTIVE = "active"
SUSPENDED = "suspended"
TERMINATED = "terminated"


def _load_active_check(store: JsonStore, emp_id: str) -> Employee:
    emp = store.load_employee(emp_id)
    if not emp:
        raise KeyError(f"员工不存在：{emp_id}")
    return emp


def suspend_employee(store: JsonStore, emp_id: str) -> Employee:
    """停职：active → suspended（可恢复）。"""
    emp = _load_active_check(store, emp_id)
    if emp.status != ACTIVE:
        raise ValueError(f"仅在职员工可停职（当前 status={emp.status}）")
    emp.status = SUSPENDED
    store.save_employee(emp)
    return emp


def activate_employee(store: JsonStore, emp_id: str) -> Employee:
    """上岗：suspended → active；terminated 不可复活。"""
    emp = _load_active_check(store, emp_id)
    if emp.status == TERMINATED:
        raise ValueError("已解雇员工不可复职（terminated 为终态）")
    if emp.status != SUSPENDED:
        raise ValueError(f"仅停职员工可上岗（当前 status={emp.status}）")
    emp.status = ACTIVE
    store.save_employee(emp)
    return emp


def terminate_employee(store: JsonStore, emp_id: str) -> Employee:
    """解雇：active/suspended → terminated（不可逆）。"""
    emp = _load_active_check(store, emp_id)
    if emp.status == TERMINATED:
        raise ValueError("员工已解雇，不可重复操作")
    emp.status = TERMINATED
    store.save_employee(emp)
    return emp
