"""企业微信渠道（骨架桩）：接口已就位，接入真实 API 时补下面 TODO。

接入步骤：
  1. 企微管理后台 → 应用管理 → 创建自建应用，拿 CorpID / AgentId / Secret
  2. 环境变量：
       LAOBAN_WECOM_CORP_ID   企业 ID
       LAOBAN_WECOM_AGENT_ID  应用 AgentId
       LAOBAN_WECOM_SECRET    应用 Secret
  3. 回调：应用「接收消息」配置 URL + Token + EncodingAESKey
       LAOBAN_WECOM_TOKEN / LAOBAN_WECOM_AES_KEY
  4. 补全 TODO（均为零依赖 urllib + 标准库实现，仿 feishu.py）：
     - _token()：GET {base}/cgi-bin/gettoken?corpid=..&corpsecret=..
     - send_text()：POST {base}/cgi-bin/message/send?access_token=..，
       body {touser, msgtype:"text", agentid, text:{content}}
     - handle()：回调 URL 验证（解密 echostr）+ 消息事件（AES-CBC 解密 XML）
     - fetch_users/fetch_departments()：cgi-bin/user/list 与 department/list

本骨架默认「未接入」：send_text 返回 False、handle 返回 501，不抛异常，
保证上层（ChannelHub）对未接入渠道安全降级。
"""
from __future__ import annotations

import os

from .channel import IMChannel

DEFAULT_BASE = "https://qyapi.weixin.qq.com"


class WeComChannel(IMChannel):
    """企业微信渠道。未提供凭证时仅占位，不做任何网络调用。"""

    platform = "wecom"

    def __init__(self, corp_id: str = "", agent_id: str = "", secret: str = "",
                 base_url: str = "", token: str = "", aes_key: str = ""):
        self.corp_id = corp_id
        self.agent_id = agent_id
        self.secret = secret
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self._token = token
        self._aes_key = aes_key

    @property
    def configured(self) -> bool:
        return bool(self.corp_id and self.secret and self.agent_id)

    # ---- 出站 ----
    def send_text(self, im_user: str, text: str) -> bool:
        # TODO(wecom): 获取 access_token 后调用 message/send（见模块 docstring）
        print(f"[IM:wecom] 渠道未接入，未推送（{im_user}）")
        return False

    def send_text_chat(self, chat_id: str, text: str) -> bool:
        return False

    # ---- 入站 ----
    def handle(self, body, background: bool = True) -> tuple[int, dict]:
        # TODO(wecom): URL 验证 + 消息解密（AES-CBC + EncodingAESKey）
        return 501, {"error": "企业微信渠道尚未接入真实回调（接口已就位）"}

    # ---- 通讯录 ----
    def supports_directory(self) -> bool:
        return self.configured

    def fetch_departments(self) -> list[dict]:
        # TODO(wecom): GET {base}/cgi-bin/department/list
        return []

    def fetch_users(self, dept_id=None) -> list[dict]:
        # TODO(wecom): GET {base}/cgi-bin/user/list?department_id=..&fetch_child=1
        return []


def wecom_from_env() -> WeComChannel | None:
    """凭证齐全则构建渠道，否则 None（未接入）。"""
    corp = os.environ.get("LAOBAN_WECOM_CORP_ID", "").strip()
    agent = os.environ.get("LAOBAN_WECOM_AGENT_ID", "").strip()
    secret = os.environ.get("LAOBAN_WECOM_SECRET", "").strip()
    if not (corp and agent and secret):
        return None
    return WeComChannel(
        corp, agent, secret,
        base_url=os.environ.get("LAOBAN_WECOM_BASE_URL", "").strip(),
        token=os.environ.get("LAOBAN_WECOM_TOKEN", "").strip(),
        aes_key=os.environ.get("LAOBAN_WECOM_AES_KEY", "").strip())
