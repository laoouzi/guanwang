"""飞书适配：事件回调（入站）+ 消息 API（出站）。

环境变量：
  LAOBAN_FEISHU_APP_ID / LAOBAN_FEISHU_APP_SECRET   必填（两者齐全才启用）
  LAOBAN_FEISHU_BASE_URL       可选（默认 https://open.feishu.cn，测试/代理可覆盖）
  LAOBAN_FEISHU_VERIFICATION_TOKEN  可选（事件 token 校验，配置后不匹配返回 403）
  LAOBAN_IM_DEFAULT_TO         可选（IM 消息不写「同事id:」时的默认收件人）

事件格式：飞书 2.0（schema=2.0，im.message.receive_v1，明文模式）。
ACK 策略：收到事件立即 200（飞书要求 3 秒内 ACK），LLM 回信放后台线程生成，
生成后经消息 API 推回；event_id 去重防重试风暴。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections import deque

from ..core.store import JsonStore
from ..llm.gateway import LLMGateway
from .binding import Bindings
from .router import route_inbound

DEFAULT_BASE = "https://open.feishu.cn"
_SEEN_CAP = 512   # event_id 去重窗口


class FeishuError(Exception):
    """飞书 API 调用失败。"""


class FeishuClient:
    """零依赖飞书 API 客户端：tenant_access_token 缓存 + 文本消息发送。"""

    def __init__(self, app_id: str, app_secret: str, base_url: str = "",
                 verification_token: str = "", timeout: int = 30):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.verification_token = verification_token
        self.timeout = timeout
        self._token = ""
        self._token_expire_at = 0.0

    def _post(self, path: str, payload: dict, token: str = "") -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            self.base_url + path, data=json.dumps(payload).encode(),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_expire_at - 60:
            return self._token
        body = self._post("/open-apis/auth/v3/tenant_access_token/internal",
                          {"app_id": self.app_id, "app_secret": self.app_secret})
        token = body.get("tenant_access_token", "")
        if not token:
            raise FeishuError(f"获取 tenant_access_token 失败：{body}")
        self._token = token
        self._token_expire_at = time.time() + float(body.get("expire", 3600))
        return token

    def send_text(self, open_id: str, text: str) -> dict:
        token = self._tenant_token()
        body = self._post(
            "/open-apis/im/v1/messages?receive_id_type=open_id",
            {"receive_id": open_id, "msg_type": "text",
             "content": json.dumps({"text": text}, ensure_ascii=False)},
            token=token)
        if body.get("code", 0) != 0:
            raise FeishuError(f"飞书发送失败 code={body.get('code')} "
                              f"msg={body.get('msg')}")
        return body


class FeishuWebhook:
    """飞书事件回调处理器：URL 验证 + 消息事件 → router → 回信推回。"""

    def __init__(self, store: JsonStore, gateway: LLMGateway | None,
                 client: FeishuClient, bindings: Bindings,
                 verification_token: str = "", default_to: str = ""):
        self.store = store
        self.gateway = gateway
        self.client = client
        self.bindings = bindings
        self.default_to = default_to
        self._token = verification_token or getattr(client, "verification_token", "")
        self._seen: deque[str] = deque(maxlen=_SEEN_CAP)
        self._seen_set: set[str] = set()

    def handle(self, body: dict, background: bool = True) -> tuple[int, dict]:
        """处理一条事件 JSON，返回 (HTTP 状态码, 响应体)。"""
        if body.get("type") == "url_verification":
            return 200, {"challenge": body.get("challenge", "")}

        header = body.get("header", {})
        if self._token and header.get("token") != self._token:
            return 403, {"error": "verification token 不匹配"}

        if header.get("event_type") != "im.message.receive_v1":
            return 200, {"code": 0}

        event_id = header.get("event_id", "")
        if event_id:
            if event_id in self._seen_set:
                return 200, {"code": 0, "msg": "duplicate"}
            self._seen.append(event_id)
            self._seen_set.add(event_id)
            if len(self._seen) == _SEEN_CAP:
                self._seen_set = set(self._seen)

        event = body.get("event", {})
        sender_id = event.get("sender", {}).get("sender_id", {})
        im_user = (sender_id.get("open_id") or sender_id.get("user_id") or "").strip()
        message = event.get("message", {})
        if message.get("message_type") != "text":
            if im_user:
                self._notify(im_user, "暂仅支持文本消息")
            return 200, {"code": 0}
        try:
            text = json.loads(message.get("content", "{}")).get("text", "")
        except (json.JSONDecodeError, TypeError, AttributeError):
            text = ""
        if not text.strip() or not im_user:
            return 200, {"code": 0}

        work = lambda: self._process(im_user, text)   # noqa: E731
        if background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
        return 200, {"code": 0}

    def _process(self, im_user: str, text: str) -> None:
        try:
            result = route_inbound(self.store, self.gateway, self.bindings,
                                   "feishu", im_user, text,
                                   push=self.client.send_text,
                                   default_to=self.default_to)
            print(f"[IM:feishu] {result.get('summary', '')}")
        except Exception as e:   # 渠道线程兜底：任何异常都不能静默丢消息
            print(f"[IM:feishu] 处理失败（{im_user}）：{e!r}")
            self._notify(im_user, f"⚠️ 处理失败：{e}")

    def _notify(self, im_user: str, text: str) -> None:
        try:
            self.client.send_text(im_user, text)
        except Exception as e:
            print(f"[IM:feishu] 推送失败（{im_user}）：{e!r}")


def feishu_from_env() -> FeishuClient | None:
    """LAOBAN_FEISHU_APP_ID/SECRET 齐全则构建客户端，否则 None。"""
    app_id = os.environ.get("LAOBAN_FEISHU_APP_ID", "").strip()
    secret = os.environ.get("LAOBAN_FEISHU_APP_SECRET", "").strip()
    if not (app_id and secret):
        return None
    return FeishuClient(
        app_id, secret,
        base_url=os.environ.get("LAOBAN_FEISHU_BASE_URL", "").strip(),
        verification_token=os.environ.get(
            "LAOBAN_FEISHU_VERIFICATION_TOKEN", "").strip())
