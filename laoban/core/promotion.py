"""员工晋升通道（双轴）：表现好的员工自动申请晋升，老板审批后生效。

两条晋升轴（触发条件、审批流、防重复、驳回冷却全部复用）：
- AI 员工 → 自主等级轴：supervised → semi → full（风险放行范围扩大，
  关系工具调用审批矩阵，必须人工把关）；
- 人类员工 → 管理权限轴：role staff → manager（晋升即放权——RBAC 的
  视图/派单/验收/编制申请权限即时扩大，因为 role_of 按 permissions.role 判定）。

共同规则：
- 触发：最近 PROMO_STREAK 条经验全部 success（连续验收通过）；
- 防重复：已有 pending 申请不重复提；已授予的目标不再申请；
- 冷却：被驳回后需再攒 PROMO_STREAK 条新 success 才可重新申请。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .employee import Employee
from .store import JsonStore

LEVELS = ("supervised", "semi", "full")   # AI 自主等级链
MANAGER_ROLE = "manager"
PROMO_STREAK = 3          # 连续 N 条 success 经验即可申请
PROMO_TYPE = "晋升申请"


@dataclass
class PromotionRequest:
    """晋升申请单（落审批日志 request 字段）。"""
    id: str
    type: str = PROMO_TYPE
    risk: str = "medium"
    requester: str = ""
    summary: str = ""
    status: str = "pending"
    approver: str = ""
    target_level: str = "semi"     # AI: semi/full；人类: manager
    streak_mark: int = 0           # 申请时的经验总数（驳回冷却用）
    created_at: float = field(default_factory=time.time)


def maybe_request_promotion(store: JsonStore, emp: Employee, log=None,
                            role: str = "") -> dict | None:
    """验收后调用：满足条件则自动提交晋升申请，返回申请摘要或 None。

    role：员工当前 RBAC 角色（staff/manager/admin）。人类晋升目标 role=manager，
    已是 manager/admin 则不申请；由调用方传入避免 core 反向依赖 dashboard。
    """
    if emp.kind == "ai":
        target, current_desc = _ai_target(emp)
    else:
        target = MANAGER_ROLE if role in ("", "staff") else None
        current_desc = "staff"
    if target is None:
        return None

    exps = emp.memory.get("experiences", [])
    if not _streak_ok(exps):
        return None
    if log is None:
        from ..runner.approval_log import ApprovalLog
        log = ApprovalLog(store)
    if _blocked_by_history(log, emp, target, len(exps)):
        return None

    summary = (f"{emp.name}（{emp.id}）连续 {PROMO_STREAK} 次验收通过，"
               + (f"申请自主等级 {current_desc} → {target}" if emp.kind == "ai"
                  else f"申请晋升部门负责人（{current_desc} → {target}，"
                       "晋升后可派单/验收/申请编制）"))
    req = PromotionRequest(
        id=f"AP-{uuid.uuid4().hex[:8]}",
        requester=emp.id, target_level=target,
        streak_mark=len(exps), summary=summary,
    )
    log_id = log.log_request(req)
    return {"id": log_id, "requester": emp.id, "target_level": target,
            "summary": summary}


def _ai_target(emp: Employee) -> tuple[str | None, str]:
    """AI 晋升目标：当前等级的下一级；full 封顶。"""
    current = emp.permissions.get("autonomy_level", "supervised")
    if current not in LEVELS or current == LEVELS[-1]:
        return None, current
    return LEVELS[LEVELS.index(current) + 1], current


def _streak_ok(exps: list[dict]) -> bool:
    """最近 PROMO_STREAK 条经验全部 success。"""
    if len(exps) < PROMO_STREAK:
        return False
    return all(e.get("outcome") == "success" for e in exps[-PROMO_STREAK:])


def _blocked_by_history(log, emp: Employee, target: str,
                        exp_count: int) -> bool:
    """历史申请防重复 / 已授予 / 驳回冷却。"""
    entries = [e for e in log.list_logs(requester=emp.id)
               if e.request.get("type") == PROMO_TYPE]
    if not entries:
        return False
    last = max(entries, key=lambda e: e.request.get("created_at", 0.0))
    status = last.request.get("status", "pending")
    if status == "pending":
        return True
    if last.request.get("target_level") == target:
        if status == "approved":
            return True
        if exp_count < last.request.get("streak_mark", 0) + PROMO_STREAK:
            return True      # 驳回冷却：再攒 3 条新 success
    return False


def apply_promotion(store: JsonStore, request: dict) -> dict | None:
    """老板审批通过后生效：AI 写回 autonomy_level；人类写回 permissions.role。"""
    emp = store.load_employee(request.get("requester", ""))
    if not emp:
        return None
    target = request.get("target_level", "")
    if emp.kind == "ai":
        if target not in LEVELS:
            return None
        emp.permissions["autonomy_level"] = target
        result = {"emp_id": emp.id, "autonomy_level": target,
                  "message": f"{emp.id} 自主等级升至 {target}"}
    else:
        if target != MANAGER_ROLE:
            return None
        emp.permissions["role"] = MANAGER_ROLE
        result = {"emp_id": emp.id, "role": MANAGER_ROLE,
                  "message": f"{emp.id} 晋升部门负责人（RBAC 权限即时生效）"}
    store.save_employee(emp)
    return result
