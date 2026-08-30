from __future__ import annotations

import uuid
from typing import Any

from .employee import Employee
from .permission import require_message
from .task import utcnow
from .store import JsonStore


def _msg_path(store: JsonStore, msg_id: str):
    d = store.root / "messages"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{msg_id}.json"


def send(store: JsonStore, from_id: str, to_id: str, content: str,
         task_id: str = "") -> dict[str, Any]:
    """点对点消息：权限校验（collaboration 白名单，空 = 组织内默认开放）→ 落盘。"""
    if not content.strip():
        raise ValueError("消息内容不能为空")
    sender = store.load_employee(from_id)
    if not sender:
        raise KeyError(f"发件员工不存在：{from_id}")
    if sender.status != "active":
        raise ValueError(f"非在职员工不可发消息（status={sender.status}）")
    receiver = store.load_employee(to_id)
    if not receiver:
        raise KeyError(f"收件员工不存在：{to_id}")
    require_message(sender, to_id)
    msg = {
        "id": f"MSG-{uuid.uuid4().hex[:6]}",
        "from": from_id, "to": to_id, "content": content,
        "task_id": task_id, "created_at": utcnow(),
    }
    store._atomic_write(_msg_path(store, msg["id"]), msg)
    return msg


def _list(store: JsonStore, key: str, who: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    d = store.root / "messages"
    if not d.exists():
        return out
    for p in d.glob("*.json"):
        msg = store._read_json(p)
        if msg and msg.get(key) == who:
            out.append(msg)
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)  # 最新在前
    return out


def inbox(store: JsonStore, who: str) -> list[dict[str, Any]]:
    return _list(store, "to", who)


def sent(store: JsonStore, who: str) -> list[dict[str, Any]]:
    return _list(store, "from", who)
