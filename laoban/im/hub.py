"""渠道中枢：多 IM 渠道统一注册 / 入站分发 / 出站路由。

上层（DashboardServer / UrgeCenter / MessageNotifier / 组织同步）只跟
ChannelHub 打交道，不关心具体有哪些平台。新增渠道 = 实现 IMChannel +
调用 hub.register(channel) 一处注册，入站与出站即自动贯通。
"""
from __future__ import annotations

from .binding import Bindings
from .channel import IMChannel, SenderChannel


class ChannelHub:
    def __init__(self, channels: list[IMChannel] | None = None,
                 bindings: Bindings | None = None):
        self.channels: dict[str, IMChannel] = {}
        self.bindings = bindings
        for ch in (channels or []):
            self.register(ch)

    def register(self, channel: IMChannel) -> None:
        self.channels[channel.platform] = channel

    def get(self, platform: str) -> IMChannel | None:
        return self.channels.get(platform)

    def platforms(self) -> list[str]:
        return list(self.channels.keys())

    # ---- 入站：webhook 按平台分发 ----
    def handle(self, platform: str, body, background: bool = True) -> tuple[int, dict]:
        ch = self.channels.get(platform)
        if ch is None:
            return 404, {"error": f"未知 IM 平台：{platform}"}
        return ch.handle(body, background=background)

    # ---- 出站：按绑定路由到对应渠道 ----
    def push_employee(self, employee_id: str, text: str) -> bool:
        """给员工推 IM 摘要/催办信：遍历已注册渠道，凡该员工在此平台有
        绑定的都推一次（多渠道触达）。返回是否至少推成功一次。"""
        if not self.bindings:
            return False
        pushed = False
        for platform, ch in self.channels.items():
            im_user = self.bindings.lookup_by_employee(platform, employee_id)
            if not im_user:
                continue
            if ch.send_text(im_user, text):
                pushed = True
        return pushed

    def push_to(self, platform: str, im_user: str, text: str) -> bool:
        """指定平台指定账号直推（入站回信/中转场景，渠道内部自己用）。"""
        ch = self.channels.get(platform)
        if ch is None:
            return False
        return ch.send_text(im_user, text)


def build_hub(feishu=None, wecom=None, dingtalk=None,
              channels: list | None = None,
              bindings: Bindings | None = None) -> ChannelHub:
    """把各渠道对象（None 则跳过）装配成一个 ChannelHub。

    - feishu / wecom / dingtalk：兼容旧的单渠道参数；已实现 IMChannel 的
      对象直接注册，仅有 send_text 的旧客户端自动包 SenderChannel；
    - channels：额外的 IMChannel 列表（按 platform 注册）。
    """
    hub = ChannelHub(bindings=bindings)
    for platform, obj in (("feishu", feishu), ("wecom", wecom),
                          ("dingtalk", dingtalk)):
        if obj is None:
            continue
        hub.register(obj if isinstance(obj, IMChannel)
                     else SenderChannel(platform, obj))
    for ch in (channels or []):
        if isinstance(ch, IMChannel):
            hub.register(ch)
    return hub
