"""员工晋升通道：表现好的 AI 员工自动申请提升自主等级（autonomy_level）。

规则：
- 触发：AI 员工最近 PROMO_STREAK 条经验全部 success（连续验收通过）；
- 等级链：supervised → semi → full，一次升一级，full 封顶；
- 申请进审批队列（type=晋升申请，risk=medium），老板批了才生效——
  自主等级关系风险放行矩阵，必须人工把关；
- 防重复：已有 pending 申请不重复提；已授予的等级不再申请；
  被驳回后需再攒 PROMO_STREAK 条新 success 才可重新申请（冷却）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .employee import Employee
from .store import JsonStore

LEVELS = ("supervised", "semi", "full")
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
    target_level: str = "semi"
    streak_mark: int = 0      # 申请时的经验总数（驳回冷却用）
    created_at: float = field(default_factory=time.time)


def maybe_request_promotion(store: JsonStore, emp: Employee, log=None) -> dict | None:
    """验收后调用：满足条件则自动提交晋升申请，返回申请摘要或 None。"""
    if emp.kind != "ai":
        return None
    current = emp.permissions.get("autonomy_level", "supervised")
    if current not in LEVELS or current == LEVELS[-1]:
        return None
    exps = emp.memory.get("experiences", [])
    if len(exps) < PROMO_STREAK:
        return None
    if not all(e.get("outcome") == "success"
               for e in exps[-PROMO_STREAK:]):
        return None

    target = LEVELS[LEVELS.index(current) + 1]
    if log is None:
        from ..runner.approval_log import ApprovalLog
        log = ApprovalLog(store)

    # 历史申请防重复 / 冷却
    entries = [e for e in log.list_logs(requester=emp.id)
               if e.request.get("type") == PROMO_TYPE]
    if entries:
        last = max(entries, key=lambda e: e.request.get("created_at", 0.0))
        status = last.request.get("status", "pending")
        if status == "pending":
            return None
        if last.request.get("target_level") == target:
            if status == "approved":
                return None                     # 已授予
            if len(exps) < last.request.get("streak_mark", 0) + PROMO_STREAK:
                return None                     # 驳回冷却：再攒 3 条新 success

    req = PromotionRequest(
        id=f"AP-{uuid.uuid4().hex[:8]}",
        requester=emp.id,
        target_level=target,
        streak_mark=len(exps),
        summary=(f"{emp.name}（{emp.id}）连续 {PROMO_STREAK} 次验收通过，"
                 f"申请自主等级 {current} → {target}"),
    )
    log_id = log.log_request(req)
    return {"id": log_id, "requester": emp.id, "target_level": target,
            "summary": req.summary}


def apply_promotion(store: JsonStore, request: dict) -> dict | None:
    """老板审批通过后生效：写回员工 autonomy_level。"""
    emp = store.load_employee(request.get("requester", ""))
    if not emp:
        return None
    target = request.get("target_level", "")
    if target not in LEVELS:
        return None
    emp.permissions["autonomy_level"] = target
    store.save_employee(emp)
    return {"emp_id": emp.id, "autonomy_level": target}
