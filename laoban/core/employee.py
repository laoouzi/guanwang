from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Employee:
    id: str
    name: str
    title: str = ""
    department: str = ""
    status: str = "active"
    permissions: dict[str, Any] = field(default_factory=lambda: {"collaboration": []})
    capabilities: dict[str, Any] = field(default_factory=lambda: {"tools": []})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "department": self.department,
            "status": self.status,
            "permissions": self.permissions,
            "capabilities": self.capabilities,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Employee":
        return cls(
            id=d["id"],
            name=d["name"],
            title=d.get("title", ""),
            department=d.get("department", ""),
            status=d.get("status", "active"),
            permissions=d.get("permissions", {"collaboration": []}),
            capabilities=d.get("capabilities", {"tools": []}),
        )
