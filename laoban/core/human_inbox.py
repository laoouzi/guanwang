from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .store import JsonStore


@dataclass
class HumanTask:
    id: str
    task_id: str
    title: str
    assignee: str
    deliverable_format: str = ""
    status: str = "pending"       # pending → completed
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "task_id": self.task_id, "title": self.title,
            "assignee": self.assignee, "deliverable_format": self.deliverable_format,
            "status": self.status, "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HumanTask":
        return cls(
            id=d["id"], task_id=d.get("task_id", ""), title=d.get("title", ""),
            assignee=d.get("assignee", ""), deliverable_format=d.get("deliverable_format", ""),
            status=d.get("status", "pending"), result=d.get("result", ""),
        )


class HumanInbox:
    """人类待办收件箱：AI 派发的人类子任务在此认领、填写结果、交还。"""

    def __init__(self, store: JsonStore):
        self.store = store
        self.dir = store.root / "human_tasks"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str):
        return self.dir / f"{task_id}.json"

    def create(self, task_id: str, title: str, assignee: str, deliverable_format: str = "") -> HumanTask:
        ht = HumanTask(id=f"HT-{uuid.uuid4().hex[:6]}", task_id=task_id,
                       title=title, assignee=assignee, deliverable_format=deliverable_format)
        self.store._atomic_write(self._path(ht.id), ht.to_dict())
        return ht

    def list_pending(self) -> list[HumanTask]:
        out = []
        for p in self.dir.glob("*.json"):
            d = self.store._read_json(p)
            if d and d.get("status") == "pending":
                out.append(HumanTask.from_dict(d))
        return out

    def complete(self, task_id: str, result: str) -> None:
        d = self.store._read_json(self._path(task_id))
        if not d:
            raise KeyError(f"人类待办不存在：{task_id}")
        ht = HumanTask.from_dict(d)
        ht.status = "completed"
        ht.result = result
        self.store._atomic_write(self._path(task_id), ht.to_dict())
