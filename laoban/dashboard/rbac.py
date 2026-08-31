"""看板视图权限（RBAC-lite）：admin / manager / staff 三级。

角色判定（`role_of`）：
- `permissions.role` 显式指定（org.json 岗位模板可直接配）；
- 未指定但有人 `reports_to` 指向该员工 → 自动升级 manager；
- 其余 → staff（默认最保守）。
免鉴权模式（未设任何口令）下所有请求按 admin 处理（向后兼容）。

可见规则：
- 花名册 / 组织架构：admin 全公司全字段；manager 全公司（跨部门脱敏）；
  staff 仅本部门 + 自己（他人脱敏）。敏感字段 = permissions / memory / model_config。
- 任务列表：admin 全量；manager / staff 仅「flow_log 出现过本部门成员」的任务。
- 消息 / 人→人回传结果：仅本人或 admin（manager 也不可看下属私信）。
- 工位队列 / 当日待办：本人、admin、或本部门 manager（管理职责）。
"""
from __future__ import annotations

from ..core.employee import Employee
from ..core.store import JsonStore

ADMIN, MANAGER, STAFF = "admin", "manager", "staff"
SENSITIVE_FIELDS = ("permissions", "memory", "model_config")


def role_of(store: JsonStore, emp: Employee) -> str:
    """员工角色：显式 permissions.role > 有人向他汇报 > staff。"""
    role = str(emp.permissions.get("role", "")).strip().lower()
    if role in (ADMIN, MANAGER, STAFF):
        return role
    for e in store.list_employees():
        if e.reports_to and e.reports_to == emp.id:
            return MANAGER
    return STAFF


def dept_members(store: JsonStore, emp: Employee) -> set[str]:
    """本部门在职成员 id 集合（含本人；未分配部门 = 仅本人）。"""
    if not emp.department:
        return {emp.id}
    return {e.id for e in store.list_employees()
            if e.department == emp.department} | {emp.id}


def mask_employee(d: dict, full: bool) -> dict:
    """脱敏：full=False 时去掉敏感字段（permissions/memory/model_config）。"""
    if full:
        return d
    return {k: v for k, v in d.items() if k not in SENSITIVE_FIELDS}


def visible_employees(store: JsonStore, me: Employee | None,
                      role: str = "") -> list[dict]:
    """按角色输出花名册（已脱敏）。

    - admin：全公司，全字段；
    - manager：全公司；本部门全字段，跨部门脱敏；
    - staff：仅本部门 + 自己；自己全字段，其余脱敏。
    """
    me = me or Employee(id="", name="")
    role = role or role_of(store, me)
    members = dept_members(store, me) if me.id else set()
    out = []
    for e in store.list_employees():
        if role == ADMIN:
            out.append(e.to_dict())
        elif role == MANAGER:
            out.append(mask_employee(e.to_dict(), e.department == me.department))
        else:  # staff
            if e.id in members:
                out.append(mask_employee(e.to_dict(), e.id == me.id))
    return out


def visible_tasks(store: JsonStore, me: Employee | None,
                  role: str = "", tasks=None) -> list:
    """任务可见性：admin 全量；其余仅与本部门成员相关的任务。

    相关 = flow_log 中任一 actor 是本部门成员（含 pending 无 actor → 仅 admin）。
    """
    tasks = tasks if tasks is not None else store.list_tasks()
    if not me or role == ADMIN:
        return list(tasks)
    members = dept_members(store, me)
    out = []
    for t in tasks:
        actors = {log.get("actor", "") for log in t.flow_log}
        if actors & members:
            out.append(t)
    return out


def _can_view_personal(store: JsonStore, me: Employee | None, role: str,
                       who: str) -> bool:
    """个人数据（消息/结果）：仅本人或 admin。"""
    if not me or role == ADMIN:
        return True
    return who == me.id


def _can_view_dept_scoped(store: JsonStore, me: Employee | None, role: str,
                          who: str) -> bool:
    """部门管理数据（队列/当日待办）：本人、admin、或本部门 manager。"""
    if not me or role == ADMIN:
        return True
    if who == me.id:
        return True
    if role == MANAGER:
        return who in dept_members(store, me)
    return False


def can_view_messages(store: JsonStore, me: Employee | None,
                      role: str, who: str) -> bool:
    return _can_view_personal(store, me, role, who)


def can_view_results(store: JsonStore, me: Employee | None,
                     role: str, who: str) -> bool:
    return _can_view_personal(store, me, role, who)


def can_view_queue(store: JsonStore, me: Employee | None,
                   role: str, who: str) -> bool:
    return _can_view_dept_scoped(store, me, role, who)


def can_view_human_tasks(store: JsonStore, me: Employee | None,
                         role: str, who: str) -> bool:
    return _can_view_dept_scoped(store, me, role, who)
