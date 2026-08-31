"""钉钉渠道（骨架桩）：接口已就位，接入真实 API 时补下面 TODO。

接入步骤：
  1. 钉钉开放平台 → 创建企业内部应用，拿 AppKey / AppSecret / AgentId
  2. 环境变量：
       LAOBAN_DINGTALK_APP_KEY    应用 AppKey
       LAOBAN_DINGTALK_APP_SECRET 应用 AppSecret
       LAOBAN_DINGTALK_AGENT_ID   应用 AgentId
  3. 回调：应用「事件订阅」配置 URL + Token + AESKey
       LAOBAN_DINGTALK_TOKEN / LAOBAN_DINGTALK_AES_KEY
  4. 补全 TODO（零依赖 urllib，仿 feishu.py）：
     - _token()：GET {base}/gettoken?appkey=..&appsecret=..
     - send_text()：POST {base}/topapi/message/corpconversation/asyncsend_v2，
       body {agent_id, userid_list, msg:{msgtype:"text", text:{content}}}
     - handle()：回调验签 + 事件解密（AES-CBC）
     - fetch_users/fetch_departments()：topapi/v2/user/list 与
       topapi/v2/department/listsub

本骨架默认「未接入」：send_text 返回 False、handle 返回 501，不抛异常。
"""
from __future__ import annotations

import os

from .channel import IMChannel

DEFAULT_BASE = "https://oapi.dingtalk.com"


class DingTalkChannel(IMChannel):
    """钉钉渠道。未提供凭证时仅占位，不做任何网络调用。"""

    platform = "dingtalk"

    def __init__(self, app_key: str = "", app_secret: str = "", agent_id: str = "",
                 base_url: str = "", token: str = "", aes_key: str = ""):
        self.app_key = app_key
        self.app_secret = app_secret
        self.agent_id = agent_id
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self._token = token
        self._aes_key = aes_key

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret and self.agent_id)

    # ---- 出站 ----
    def send_text(self, im_user: str, text: str) -> bool:
        # TODO(dingtalk): 获取 access_token 后调用 asyncsend_v2（见模块 docstring）
        print(f"[IM:dingtalk] 渠道未接入，未推送（{im_user}）")
        return False

    def send_text_chat(self, chat_id: str, text: str) -> bool:
        return False

    # ---- 入站 ----
    def handle(self, body, background: bool = True) -> tuple[int, dict]:
        # TODO(dingtalk): 回调验签 + 事件解密
        return 501, {"error": "钉钉渠道尚未接入真实回调（接口已就位）"}

    # ---- 通讯录 ----
    def supports_directory(self) -> bool:
        return self.configured

    def fetch_departments(self) -> list[dict]:
        # TODO(dingtalk): GET {base}/topapi/v2/department/listsub
        return []

    def fetch_users(self, dept_id=None) -> list[dict]:
        # TODO(dingtalk): GET {base}/topapi/v2/user/list
        return []


def dingtalk_from_env() -> DingTalkChannel | None:
    """凭证齐全则构建渠道，否则 None（未接入）。"""
    key = os.environ.get("LAOBAN_DINGTALK_APP_KEY", "").strip()
    secret = os.environ.get("LAOBAN_DINGTALK_APP_SECRET", "").strip()
    agent = os.environ.get("LAOBAN_DINGTALK_AGENT_ID", "").strip()
    if not (key and secret and agent):
        return None
    return DingTalkChannel(
        key, secret, agent,
        base_url=os.environ.get("LAOBAN_DINGTALK_BASE_URL", "").strip(),
        token=os.environ.get("LAOBAN_DINGTALK_TOKEN", "").strip(),
        aes_key=os.environ.get("LAOBAN_DINGTALK_AES_KEY", "").strip())
