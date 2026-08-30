from __future__ import annotations

from .employee import Employee
from .store import JsonStore
from .state_machine import advance
from .task import Task, ASSIGNED


def enqueue(store: JsonStore, emp_id: str, task_id: str) -> list[str]:
    """任务入队员工工位（workspace.queue）。幂等：重复入队无副作用。"""
    emp = store.load_employee(emp_id)
    if not emp:
        raise KeyError(f"员工不存在：{emp_id}")
    if emp.status != "active":
        raise ValueError(f"非在职员工不可承接任务（status={emp.status}）")
    if task_id not in emp.workspace.get("queue", []):
        emp.workspace.setdefault("queue", []).append(task_id)
        store.save_employee(emp)
    return emp.workspace["queue"]


def dequeue(store: JsonStore, emp_id: str, task_id: str) -> list[str]:
    """任务出队（完成/转移时调用）。不存在的任务无操作。"""
    emp = store.load_employee(emp_id)
    if not emp:
        raise KeyError(f"员工不存在：{emp_id}")
    q = emp.workspace.get("queue", [])
    if task_id in q:
        q.remove(task_id)
        store.save_employee(emp)
    return q


def queue_of(store: JsonStore, emp_id: str) -> list[str]:
    emp = store.load_employee(emp_id)
    if not emp:
        raise KeyError(f"员工不存在：{emp_id}")
    return list(emp.workspace.get("queue", []))


def assign_task(store: JsonStore, task_id: str, emp_id: str,
                actor: str = "boss") -> Task:
    """派发：任务状态机推进到 assigned + 入队员工工位。"""
    emp = store.load_employee(emp_id)
    if not emp:
        raise KeyError(f"员工不存在：{emp_id}")
    if emp.status != "active":
        raise ValueError(f"非在职员工不可承接任务（status={emp.status}）")
    task = store.load_task(task_id)
    if not task:
        raise KeyError(f"任务不存在：{task_id}")
    advance(task, ASSIGNED, actor=actor, remark=f"派发给 {emp_id}（{emp.name}）")
    store.save_task(task)
    enqueue(store, emp_id, task_id)
    return task
