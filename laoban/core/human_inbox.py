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
    due_date: str = ""            # YYYY-MM-DD，空 = 不限期（随时可见）
    source: str = "ai_delegated"  # ai_delegated（AI 派发配合）/ self / boss
    created_by: str = "boss"      # 发起人 id：完成结果回传给谁（人→人闭环）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "task_id": self.task_id, "title": self.title,
            "assignee": self.assignee, "deliverable_format": self.deliverable_format,
            "status": self.status, "result": self.result,
            "due_date": self.due_date, "source": self.source,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HumanTask":
        return cls(
            id=d["id"], task_id=d.get("task_id", ""), title=d.get("title", ""),
            assignee=d.get("assignee", ""), deliverable_format=d.get("deliverable_format", ""),
            status=d.get("status", "pending"), result=d.get("result", ""),
            due_date=d.get("due_date", ""), source=d.get("source", "ai_delegated"),
            created_by=d.get("created_by", "boss"),
        )


class HumanInbox:
    """人类待办收件箱：AI 派发的人类子任务在此认领、填写结果、交还。

    人类员工与 AI 同部门协作：每个人类员工每天有自己的任务清单
    （daily_list 按人按天过滤，含 AI 派发的配合任务）。
    """

    def __init__(self, store: JsonStore):
        self.store = store
        self.dir = store.root / "human_tasks"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str):
        return self.dir / f"{task_id}.json"

    def create(self, task_id: str, title: str, assignee: str, deliverable_format: str = "",
               due_date: str = "", source: str = "ai_delegated",
               created_by: str = "boss") -> HumanTask:
        ht = HumanTask(id=f"HT-{uuid.uuid4().hex[:6]}", task_id=task_id,
                       title=title, assignee=assignee, deliverable_format=deliverable_format,
                       due_date=due_date, source=source, created_by=created_by)
        self.store._atomic_write(self._path(ht.id), ht.to_dict())
        return ht

    def list_pending(self) -> list[HumanTask]:
        out = []
        for p in self.dir.glob("*.json"):
            d = self.store._read_json(p)
            if d and d.get("status") == "pending":
                out.append(HumanTask.from_dict(d))
        return out

    def daily_list(self, assignee: str, date: str) -> list[HumanTask]:
        """某人类员工某天的任务清单。

        规则：
        - assignee 必须匹配（只看自己的活）；
        - 未完成（pending）；
        - 无截止日期 → 随时可见；
        - 截止日期 <= date → 可见（逾期的仍留在清单里继续处理）；
        - 截止日期 > date（未来）→ 不出现在今天的清单。
        """
        out = []
        for ht in self.list_pending():
            if ht.assignee != assignee:
                continue
            if ht.due_date and ht.due_date > date:
                continue
            out.append(ht)
        return out

    def complete(self, task_id: str, result: str) -> None:
        d = self.store._read_json(self._path(task_id))
        if not d:
            raise KeyError(f"人类待办不存在：{task_id}")
        ht = HumanTask.from_dict(d)
        ht.status = "completed"
        ht.result = result
        self.store._atomic_write(self._path(task_id), ht.to_dict())

    def results_for(self, requester: str) -> list[HumanTask]:
        """某发起人已收到的回传结果（人→人闭环的结果返回）。

        规则：created_by == requester 且 status == completed。
        待办（pending）不出现——那是"还在别人手里的活"，不算回传。
        """
        out: list[HumanTask] = []
        for p in sorted(self.dir.glob("*.json")):
            d = self.store._read_json(p)
            if not d:
                continue
            if d.get("status") == "completed" and d.get("created_by") == requester:
                out.append(HumanTask.from_dict(d))
        return out
