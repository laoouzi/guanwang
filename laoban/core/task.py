from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PENDING = "pending"
TRIAGE = "triage"
PLANNING = "planning"
REVIEW = "review"
ASSIGNED = "assigned"
DOING = "doing"
WAITING_HUMAN = "waiting_human"
REPORTING = "reporting"
DONE = "done"
CANCELLED = "cancelled"
BLOCKED = "blocked"

TERMINAL_STATES = frozenset({DONE, CANCELLED, BLOCKED})


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    id: str
    title: str
    state: str = PENDING
    priority: str = "normal"
    instruction: str = ""      # 任务详细要求（Runner 送进 prompt；空 = 只有标题）
    review_round: int = 0
    block_reason: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    flow_log: list[dict[str, Any]] = field(default_factory=list)
    progress_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "priority": self.priority,
            "instruction": self.instruction,
            "review_round": self.review_round,
            "block_reason": self.block_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "flow_log": self.flow_log,
            "progress_log": self.progress_log,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            id=d["id"],
            title=d["title"],
            state=d.get("state", PENDING),
            priority=d.get("priority", "normal"),
            instruction=d.get("instruction", ""),
            review_round=d.get("review_round", 0),
            block_reason=d.get("block_reason", ""),
            created_at=d.get("created_at", utcnow()),
            updated_at=d.get("updated_at", utcnow()),
            flow_log=d.get("flow_log", []),
            progress_log=d.get("progress_log", []),
        )
