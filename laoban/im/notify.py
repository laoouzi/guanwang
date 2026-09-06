"""新消息 IM 离线推送摘要：看板红点的 IM 触达面（多渠道）。

看板启动时把 MessageNotifier 注入 messenger.set_notifier（进程级钩子），
之后任何落库的新消息（HTTP 聊天 / AI 员工主动发信 / 待办结果回传 …）
都会给「绑定了 IM 的收件人」推一条摘要——人不在看板前也能知道有新信。

渠道无关：摘要推送走 ChannelHub，按收件人的绑定自动路由到飞书 / 企微 /
钉钉等任一已接入渠道；未来新增渠道无需改本模块。

与渠道路由（router.py）的分工：router 负责 IM 入站消息的回信/中转推送；
本模块只做「消息总线新增 → 收件人 IM 摘要」。渠道路由处理入站时用
messenger.suppress_notify 抑制本钩子，避免同一条消息推两次。
"""
from __future__ import annotations

from ..core.store import JsonStore
from .binding import Bindings
from .hub import ChannelHub, build_hub

_MAX = 40   # 摘要截断长度（字符）


class MessageNotifier:
    """callable 钩子：msg dict → 收件人 IM 摘要推送。

    无渠道 / 收件人未绑定 / 推送失败 → 静默跳过（消息已落总线，
    红点照常亮，失败不算事故）。捕获所有异常也是给 messenger 的双保险。
    """

    def __init__(self, store: JsonStore, hub: ChannelHub | None = None,
                 feishu=None, webpush=None):
        self.store = store
        self.bindings = Bindings(store.root)
        # hub 优先；未给 hub 但给了 feishu（旧参数）→ 包成单渠道 hub
        self.hub = hub if hub is not None else (
            build_hub(feishu=feishu, bindings=self.bindings) if feishu else None)
        self.webpush = webpush   # 可选：Web Push 离线通知（浏览器系统通知）

    def __call__(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        to_id = msg.get("to", "")
        if not to_id:
            return
        summary = self.summary(msg)
        if self.hub is not None:
            self.hub.push_employee(to_id, summary)   # 未绑定/失败静默
        if self.webpush is not None:
            # Web Push：收件人不在看板前也能弹系统通知（真正替代 IM 触达）
            self.webpush.notify(to_id, "老板账本 · 新消息", summary)

    def summary(self, msg: dict) -> str:
        """摘要文案：谁发的 + 内容前 40 字 + 回看指引。"""
        sender = self.store.load_employee(msg.get("from", ""))
        who = f"{sender.name}（{sender.id}）" if sender else msg.get("from", "?")
        text = (msg.get("content") or "").strip().replace("\n", " ")
        body = text[:_MAX] + ("…" if len(text) > _MAX else "")
        task = f"（任务 {msg['task_id']}）" if msg.get("task_id") else ""
        return f"【新消息】{who}：{body}{task} —— 看板查看并回复"
