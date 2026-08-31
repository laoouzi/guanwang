from __future__ import annotations

from .employee import Employee
from .store import JsonStore
from .state_machine import advance
from .task import Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED


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
    task.assignee = emp_id   # 持久承接人：出队后个人计划视图仍可追溯
    store.save_task(task)
    enqueue(store, emp_id, task_id)
    return task


def assign_task_auto(store: JsonStore, task_id: str, emp_id: str,
                     actor: str = "boss") -> Task:
    """快捷派发：未到 review 的任务先自动走完前置流程再派发。

    老板/看板场景：submit 后直接指派，不关心中间状态。
    中间流转全部落 flow_log（remark 标注「直派快捷」），审计链完整；
    语义 = 派单人即评审人（跳过独立评审环节，小公司常见形态）。
    """
    emp = store.load_employee(emp_id)
    if not emp:
        raise KeyError(f"员工不存在：{emp_id}")
    if emp.status != "active":
        raise ValueError(f"非在职员工不可承接任务（status={emp.status}）")
    task = store.load_task(task_id)
    if not task:
        raise KeyError(f"任务不存在：{task_id}")
    for state in (TRIAGE, PLANNING, REVIEW):
        if task.state == state:
            continue
        if task.state not in (PENDING, TRIAGE, PLANNING):
            break   # 已在 review 或更后：交给 assign_task 的常规校验
        advance(task, state, actor=actor, remark="直派快捷（跳过独立评审）")
        store.save_task(task)
    return assign_task(store, task_id, emp_id, actor=actor)
