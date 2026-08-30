from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Employee:
    id: str
    name: str
    title: str = ""
    department: str = ""
    reports_to: str = ""
    source: str = "hired"                 # founder/template/hired/cloned
    status: str = "active"                # active/suspended/terminated
    hired_at: str = ""
    job_description: dict[str, Any] = field(default_factory=lambda: {
        "mission": "", "duties": [], "workflow_rules": [],
        "escalation": "超出能力 → 转人类待办或上报直属上级",
    })
    performance_goals: dict[str, Any] = field(default_factory=lambda: {
        "max_concurrent": 3,
        "budget_daily_tokens": 500000,
        "budget_daily_cost": 20.0,
        "quality_bar": "",
    })
    capabilities: dict[str, Any] = field(default_factory=lambda: {
        "tools": [], "skills": [], "model_fit": [],
    })
    model_config: dict[str, Any] = field(default_factory=lambda: {
        "provider": "mock", "model": "mock", "temperature": 0.3,
    })
    permissions: dict[str, Any] = field(default_factory=lambda: {
        "collaboration": [],
        "can_assign_human_tasks": False,
        "spending_limit_per_task": 5.0,
        "autonomy_level": "supervised",   # supervised/semi/full
    })
    memory: dict[str, Any] = field(default_factory=lambda: {
        "experiences": [], "notes": [],
    })
    workspace: dict[str, Any] = field(default_factory=lambda: {
        "dir": "", "queue": [], "context": {},
    })

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "department": self.department,
            "reports_to": self.reports_to,
            "source": self.source,
            "status": self.status,
            "hired_at": self.hired_at,
            "job_description": self.job_description,
            "performance_goals": self.performance_goals,
            "capabilities": self.capabilities,
            "model_config": self.model_config,
            "permissions": self.permissions,
            "memory": self.memory,
            "workspace": self.workspace,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Employee":
        e = cls(id=d["id"], name=d["name"])
        for k in ("title", "department", "reports_to", "source", "status", "hired_at"):
            if k in d:
                setattr(e, k, d[k])
        for k in ("job_description", "performance_goals", "capabilities",
                  "model_config", "permissions", "memory", "workspace"):
            if k in d and isinstance(d[k], dict):
                # 合并默认值，确保新字段也有默认兜底
                merged = dict(getattr(e, k))
                merged.update(d[k])
                setattr(e, k, merged)
        return e
