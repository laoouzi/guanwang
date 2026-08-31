from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from .employee import Employee
from .permission import require_message
from .task import utcnow
from .store import JsonStore

# 新消息通知钩子：msg 落盘后回调（msg dict）。
# 看板注入 MessageNotifier → 新信同步推 IM（红点的离线触达面）；
# 通知失败只打印不抛——消息总线是唯一事实源，通知是尽力而为。
_notifier: Callable[[dict], None] | None = None
_local = threading.local()


def set_notifier(fn: Callable[[dict], None] | None) -> None:
    """注入/清除新消息通知钩子（进程级，通常只在看板启动时设一次）。"""
    global _notifier
    _notifier = fn


class suppress_notify:
    """上下文管理器：本线程内落库的消息不再触发通知钩子。

    两类场景用：
    - IM 渠道线程处理入站消息：回信/中转由渠道路由自己推送，
      再走钩子会双推；
    - UrgeCenter 发催办信：自带定向 IM 推送（含升级链），
      钩子的摘要推送会重复。
    """

    def __enter__(self):
        self._prev = getattr(_local, "off", False)
        _local.off = True
        return self

    def __exit__(self, *exc):
        _local.off = self._prev
        return False


def _msg_path(store: JsonStore, msg_id: str):
    d = store.root / "messages"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{msg_id}.json"


def send(store: JsonStore, from_id: str, to_id: str, content: str,
         task_id: str = "") -> dict[str, Any]:
    """点对点消息：权限校验（collaboration 白名单，空 = 组织内默认开放）→ 落盘。

    新消息天然未读（无 read_at 字段）；收件人查看后由 mark_read 补记。
    落盘后触发通知钩子（未注入/被抑制/推送失败均不影响落盘结果）。
    """
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
    if _notifier is not None and not getattr(_local, "off", False):
        try:
            _notifier(msg)
        except Exception as e:   # 通知失败绝不影响消息落库
            print(f"[notify] 新消息 IM 推送失败（{to_id}）：{e!r}")
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


def unread_count(store: JsonStore, who: str) -> int:
    """未读数：收件箱中没有 read_at 的消息（红点口径）。"""
    return sum(1 for m in inbox(store, who) if not m.get("read_at"))


def mark_read(store: JsonStore, who: str) -> int:
    """收件箱全部标记已读（查看即已读）。

    逐条原子回写 read_at（幂等：已读的跳过）；
    返回本次新标记条数。
    """
    n = 0
    for m in inbox(store, who):
        if m.get("read_at"):
            continue
        m["read_at"] = utcnow()
        store._atomic_write(_msg_path(store, m["id"]), m)
        n += 1
    return n
