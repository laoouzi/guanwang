"""IM 渠道统一抽象：飞书 / 企业微信 / 钉钉 的出站推送 + 入站事件 + 通讯录。

上层（router / notify / UrgeCenter / 组织同步）面向 IMChannel 编程，不再
关心具体渠道——新增渠道只需实现本接口并注册进 ChannelHub。

接口约定：
- send_text / send_text_chat：出站，返回 bool（True=已推送）；实现内部须
  吞掉异常返回 False，绝不让推送失败炸掉上层（消息总线是唯一事实源）。
- handle：入站 webhook 事件，返回 (HTTP 状态码, 响应体 dict)。
- fetch_departments / fetch_users：通讯录拉取（组织同步用），不支持返回空。
"""
from __future__ import annotations


class IMChannel:
    """IM 渠道基类。"""

    platform = ""   # 与 Bindings.platform 一致：feishu / wecom / dingtalk

    # ---- 出站 ----
    def send_text(self, im_user: str, text: str) -> bool:
        raise NotImplementedError

    def send_text_chat(self, chat_id: str, text: str) -> bool:
        return False   # 默认不支持群聊直推（企微/钉钉应用消息一般走 DM）

    # ---- 入站 ----
    def handle(self, body, background: bool = True) -> tuple[int, dict]:
        raise NotImplementedError

    # ---- 通讯录（组织同步用）----
    def supports_directory(self) -> bool:
        return False

    def fetch_departments(self) -> list[dict]:
        return []

    def fetch_users(self, dept_id=None) -> list[dict]:
        return []
