"""员工晋升通道（双轴 · 积分驱动）：表现好的员工自动申请晋升，老板审批后生效。

两条晋升轴（触发、审批流、防重复、驳回冷却机制复用）：
- AI 员工 → 自主等级轴：supervised → semi → full（风险放行范围扩大）；
- 人类员工 → 管理权限轴：role staff → manager（晋升即放权，RBAC 即时生效，
  role_of 按 permissions.role 判定）。

触发条件（奖励积分统一驱动，方便横向对比）：
- AI：奖励积分 ≥ PROMO_POINTS 即可申请（成长快，不做年度限制）；
- 人类：入职满一年（hired_at 锚点，年度评估）且积分达标 → 自动申请；
- 防重复：已有 pending 申请不重复提；已授予的目标不再申请；
- 冷却：被驳回后需再攒 PROMO_POINTS 分才可重新申请。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .employee import Employee
from .store import JsonStore

LEVELS = ("supervised", "semi", "full")   # AI 自主等级链
MANAGER_ROLE = "manager"
PROMO_POINTS = 30.0        # 晋升积分线（3 次满分验收 = 30 分）
PROMO_TYPE = "晋升申请"
YEAR_DAYS = 365            # 人类年度评估周期


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
    points_mark: float = 0.0       # 申请时的积分（驳回冷却用）
    created_at: float = field(default_factory=time.time)


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _years_satisfied(emp: Employee) -> bool:
    """人类年度评估：入职（或上次晋升）满 YEAR_DAYS 天。"""
    anchor = _parse_date(
        emp.permissions.get("last_promoted_at") or emp.hired_at or "")
    if anchor is None:
        return False
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - anchor).days >= YEAR_DAYS


def maybe_request_promotion(store: JsonStore, emp: Employee, log=None,
                            role: str = "", ledger=None) -> dict | None:
    """验收后调用：满足条件则自动提交晋升申请，返回申请摘要或 None。

    role：员工当前 RBAC 角色（避免 core 反向依赖 dashboard，由调用方传入）。
    ledger：奖励积分账本（points(emp_id) 取当前积分）。
    """
    if emp.kind == "ai":
        target, current_desc = _ai_target(emp)
    else:
        # 人类年度评估：入职满一年 且 role=staff 才有上升空间
        if role not in ("", "staff") or not _years_satisfied(emp):
            return None
        target, current_desc = MANAGER_ROLE, "staff"
    if target is None:
        return None

    points = ledger.points(emp.id) if ledger is not None else 0.0
    if points < PROMO_POINTS:
        return None
    if log is None:
        from ..runner.approval_log import ApprovalLog
        log = ApprovalLog(store)
    if _blocked_by_history(log, emp, target, points):
        return None

    summary = (f"{emp.name}（{emp.id}）奖励积分 {points:g} 达标，"
               + (f"申请自主等级 {current_desc} → {target}" if emp.kind == "ai"
                  else f"年度评估通过，申请晋升部门负责人（{current_desc} → "
                       f"{target}，晋升后可派单/验收/申请编制）"))
    req = PromotionRequest(
        id=f"AP-{uuid.uuid4().hex[:8]}",
        requester=emp.id, target_level=target,
        points_mark=points, summary=summary,
    )
    log_id = log.log_request(req)
    return {"id": log_id, "requester": emp.id, "target_level": target,
            "points": points, "summary": summary}


def _ai_target(emp: Employee) -> tuple[str | None, str]:
    """AI 晋升目标：当前等级的下一级；full 封顶。"""
    current = emp.permissions.get("autonomy_level", "supervised")
    if current not in LEVELS or current == LEVELS[-1]:
        return None, current
    return LEVELS[LEVELS.index(current) + 1], current


def _blocked_by_history(log, emp: Employee, target: str,
                        points: float) -> bool:
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
        if points < last.request.get("points_mark", 0.0) + PROMO_POINTS:
            return True      # 驳回冷却：再攒满一档晋升积分
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
    # 记录晋升时间：人类下一次年度评估的锚点
    emp.permissions["last_promoted_at"] = datetime.now(
        timezone.utc).isoformat()
    store.save_employee(emp)
    return result
