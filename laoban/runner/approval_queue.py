from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def should_approve(risk: str, autonomy_level: str) -> bool:
    """分级放行：返回 True 表示需要人类审批。

    决策矩阵：high 永远审批；full 放行 low/medium；semi 放行 low；
    supervised 全部审批。
    """
    if risk == "high":
        return True
    if autonomy_level == "supervised":
        return True
    if autonomy_level == "semi":
        return risk == "medium"
    if autonomy_level == "full":
        return False
    return True  # 未知等级默认保守审批


@dataclass
class ApprovalRequest:
    id: str
    type: str                      # 高危操作 | 支出超限 | 编制申请
    risk: str = "high"
    priority: str = "normal"       # normal | urgent
    requester: str = ""
    summary: str = ""
    amount: float = 0.0
    status: str = "pending"        # pending → approved/rejected
    approver: str = ""
    opinion: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class ApprovalQueue:
    batch_size: int = 5
    timeout_sec: int = 120
    urgent_batch_size: int = 1
    urgent_timeout_sec: int = 30

    def __post_init__(self):
        self._normal: list[ApprovalRequest] = []
        self._urgent: list[ApprovalRequest] = []

    def enqueue(self, req: ApprovalRequest) -> None:
        (self._urgent if req.priority == "urgent" else self._normal).append(req)

    def _pop_ready(self, items: list[ApprovalRequest], size: int, timeout: int) -> list[ApprovalRequest]:
        ready = [r for r in items if r.status == "pending"]
        now = time.time()
        if len(ready) >= size or (ready and now - ready[0].created_at >= timeout):
            batch = ready[:size]
            for r in batch:
                items.remove(r)
            return batch
        return []

    def flush_if_ready(self) -> list[ApprovalRequest]:
        batch = self._pop_ready(self._urgent, self.urgent_batch_size, self.urgent_timeout_sec)
        if batch:
            return batch
        return self._pop_ready(self._normal, self.batch_size, self.timeout_sec)

    def pending_count(self) -> int:
        return len(self._normal) + len(self._urgent)
