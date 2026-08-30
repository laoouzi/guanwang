"""D6：高危操作审批记录——把审批单从内存队列持久化到磁盘，做完整审计链。

设计要点：
- `ApprovalLog(store)`：所有高危操作先走 `log_request` → 再走审批流程 → `log_decision` 记录结果。
- 不管批不批，`risk=high` 的请求 100% 产生一条磁盘记录（D6 硬保障）。
- 查询接口 `list_logs(requester=, risk=, status=)` 用于看板/审计。
- 封装 `request_and_maybe_block(emp, tool, args, guard, queue, log)`：
    → 风险分级 → 需要审批则入队 + 持久化 → 阻塞直到 `status != pending`。
"""
from __future__ import annotations

import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.store import JsonStore
from .approval_queue import ApprovalQueue, ApprovalRequest, should_approve
from .guard import classify_risk


@dataclass
class ApprovalLogEntry:
    id: str
    request: dict[str, Any]              # ApprovalRequest.asdict
    decided_at: float = 0.0              # 审批时间
    approver: str = ""
    opinion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request": self.request,
            "decided_at": self.decided_at,
            "approver": self.approver,
            "opinion": self.opinion,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApprovalLogEntry":
        return cls(
            id=d["id"], request=d.get("request", {}),
            decided_at=d.get("decided_at", 0.0),
            approver=d.get("approver", ""),
            opinion=d.get("opinion", ""),
        )


class ApprovalLog:
    """磁盘审批日志。D6 关键保障：risk=high 必有落盘记录。"""

    def __init__(self, store: JsonStore):
        self.store = store
        self.dir = store.root / "approvals"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, log_id: str) -> Path:
        return self.dir / f"{log_id}.json"

    def log_request(self, req: ApprovalRequest) -> str:
        """记录审批请求，返回 log_id。"""
        log_id = req.id or f"AP-{uuid.uuid4().hex[:8]}"
        entry = ApprovalLogEntry(id=log_id, request=asdict(req))
        self.store._atomic_write(self._path(log_id), entry.to_dict())
        return log_id

    def log_decision(self, log_id: str, approver: str, approved: bool,
                     opinion: str = "") -> None:
        import time
        d = self.store._read_json(self._path(log_id))
        if not d:
            raise KeyError(f"审批日志不存在：{log_id}")
        entry = ApprovalLogEntry.from_dict(d)
        entry.decided_at = time.time()
        entry.approver = approver
        entry.opinion = opinion or ("通过" if approved else "驳回")
        # request 里同步写 status，便于从 entry 一眼看结果
        entry.request["status"] = "approved" if approved else "rejected"
        entry.request["approver"] = approver
        self.store._atomic_write(self._path(log_id), entry.to_dict())

    def list_logs(self, requester: str = "", risk: str = "", status: str = "") -> list[ApprovalLogEntry]:
        out: list[ApprovalLogEntry] = []
        for p in sorted(self.dir.glob("*.json")):
            d = self.store._read_json(p)
            if not d:
                continue
            e = ApprovalLogEntry.from_dict(d)
            if requester and e.request.get("requester") != requester:
                continue
            if risk and e.request.get("risk") != risk:
                continue
            if status and e.request.get("status") != status:
                continue
            out.append(e)
        return out


def request_and_maybe_block(
    emp, tool: str, args: dict[str, Any],
    queue: ApprovalQueue, log: ApprovalLog,
) -> tuple[bool, str, str]:
    """执行高危操作前调用。

    返回 (需要审批吗, log_id, 初始判定理由)。
    - 需要审批：返回 (True, log_id, 理由)，调用方需后续 `log_decision`。
    - 可自动放行（低风险 + 高自主）：返回 (False, "", 理由)。
    - ⚠️ risk=high 仍会产生日志（即使最后自动放行——但 high 一定需要审批，不会自动放行）。
    """
    risk = classify_risk(tool, args)
    autonomy = emp.permissions.get("autonomy_level", "supervised")
    need = should_approve(risk, autonomy)

    req = ApprovalRequest(
        id=f"AP-{uuid.uuid4().hex[:8]}",
        type=("高危操作" if risk == "high" else ("支出超限" if risk == "medium" else "常规操作")),
        risk=risk, requester=emp.id,
        summary=f"工具 {tool} args={args}",
    )
    # D6 硬保障：risk=high 一定落日志；need=False 的 low 也记一笔备查（不强制）
    if need or risk == "high":
        log_id = log.log_request(req)
    else:
        log_id = log.log_request(req)  # 全记，审计更完整
    if need:
        queue.enqueue(req)
        return True, log_id, f"风险={risk} 自主={autonomy}，需审批"
    return False, log_id, f"风险={risk} 自主={autonomy}，自动放行"
