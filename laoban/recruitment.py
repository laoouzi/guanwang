from __future__ import annotations

import uuid

from .core.store import JsonStore
from .core.employee import Employee
from .org import build_employee, find_role, load_org_for_store

HIRE_TYPES = ("new_ai", "clone_ai", "hire_human")


def _req_path(store: JsonStore, req_id: str):
    d = store.root / "headcount_requests"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{req_id}.json"


def submit_headcount_request(store: JsonStore, requester: str, reason: str,
                             headcount: int, role: str = "", cost: float = 0.0,
                             hire_type: str = "new_ai", department: str = "",
                             source_emp_id: str = "") -> dict:
    """轨道 B：部门负责人提交编制申请（新增 AI / 复制 AI / 招聘人类）。"""
    if not reason.strip():
        raise ValueError("编制申请必须附理由")
    if hire_type not in HIRE_TYPES:
        raise ValueError(f"hire_type 必须是 {HIRE_TYPES} 之一，收到：{hire_type}")
    if hire_type == "clone_ai" and not source_emp_id:
        raise ValueError("复制 AI 必须指定 source_emp_id")
    req_id = f"HR-{uuid.uuid4().hex[:6]}"
    req = {
        "id": req_id, "requester": requester, "reason": reason,
        "headcount": headcount, "role": role, "cost": cost,
        "hire_type": hire_type, "department": department,
        "source_emp_id": source_emp_id, "status": "pending", "approver": "",
    }
    store._atomic_write(_req_path(store, req_id), req)
    return req


def get_request(store: JsonStore, req_id: str) -> dict | None:
    return store._read_json(_req_path(store, req_id))


def approve_headcount(store: JsonStore, req_id: str, approver: str) -> Employee:
    """老板审批通过 → HR 执行入职，返回新员工。"""
    req = get_request(store, req_id)
    if not req:
        raise KeyError(f"编制申请不存在：{req_id}")
    if req["status"] != "pending":
        raise ValueError(f"申请已处理（status={req['status']}），不可重复审批")

    hire_type = req["hire_type"]
    if hire_type == "clone_ai":
        src = store.load_employee(req["source_emp_id"])
        if not src:
            raise KeyError(f"复制源员工不存在：{req['source_emp_id']}")
        d = src.to_dict()
        emp = Employee.from_dict(d)
        emp.id = f"emp-{uuid.uuid4().hex[:6]}"
        emp.name = f"{src.name}·分身"
        emp.source = "cloned"
        if req.get("department"):
            emp.department = req["department"]
    else:
        # v0.2：role 命中 org.json 岗位模板 → 套用模板（模型/权限/职责）入职
        emp_id = f"emp-{uuid.uuid4().hex[:6]}"
        template = find_role(load_org_for_store(store), req.get("role", ""))
        if template:
            dept, role = template
            emp = build_employee(dept, role)
            emp.id = emp_id
            emp.name = role["name"]
            emp.kind = "human" if hire_type == "hire_human" else "ai"
            if req.get("department"):
                emp.department = req["department"]
            emp.source = "hired"
        else:
            emp = Employee(
                id=emp_id,
                name=f"新员工-{req_id[-6:]}",
                kind="human" if hire_type == "hire_human" else "ai",
                title=req.get("role", ""),
                department=req.get("department", ""),
                source="hired",
            )
    store.save_employee(emp)

    req["status"] = "approved"
    req["approver"] = approver
    req["hired_emp_id"] = emp.id
    store._atomic_write(_req_path(store, req_id), req)
    return emp
