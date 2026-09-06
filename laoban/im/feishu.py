"""飞书适配：事件回调（入站）+ 消息 API（出站）。

环境变量：
  LAOBAN_FEISHU_APP_ID / LAOBAN_FEISHU_APP_SECRET   必填（两者齐全才启用）
  LAOBAN_FEISHU_BASE_URL       可选（默认 https://open.feishu.cn，测试/代理可覆盖）
  LAOBAN_FEISHU_VERIFICATION_TOKEN  可选（事件 token 校验，配置后不匹配返回 403）
  LAOBAN_FEISHU_ENCRYPT_KEY    可选（事件加密，AES-256-CBC，key=SHA256(encrypt_key)）
  LAOBAN_FEISHU_BOT_OPEN_ID    可选（群聊 @提及识别；未配置时任何 @ 都视为 @机器人）
  LAOBAN_IM_DEFAULT_TO         可选（IM 消息不写「同事id:」时的默认收件人）

事件格式：飞书 2.0（schema=2.0，im.message.receive_v1，明文或加密模式）。
群聊规则：chat_type=group 且 @了机器人才处理（否则忽略不吵群）；@提及
token（@_user_1）从文本剥离后再解析「同事id: 内容」；回信/错误提示/投递
回执推回群（chat_id），人→人中转仍走对方 DM。
ACK 策略：收到事件立即 200（飞书要求 3 秒内 ACK），LLM 回信放后台线程生成，
生成后经消息 API 推回；event_id 去重防重试风暴。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.request
from collections import deque

from ..core.store import JsonStore
from ..llm.gateway import LLMGateway
from .binding import Bindings
from .channel import IMChannel
from .router import route_inbound

DEFAULT_BASE = "https://open.feishu.cn"
_SEEN_CAP = 512   # event_id 去重窗口

# 可选加密库：pycryptodome 或 cryptography 任一即可（都缺失时加密事件不可用）
_AES = None
_unpad = None
try:
    from Crypto.Cipher import AES as _AES
    from Crypto.Util.Padding import unpad as _unpad
    _HAS_CRYPTO = True
except ImportError:
    try:
        from cryptography.hazmat.primitives.ciphers import (Cipher as _Cipher,
            algorithms as _alg, modes as _modes)
        from cryptography.hazmat.primitives.padding import PKCS7 as _PKCS7
        _HAS_CRYPTO = True
    except ImportError:
        _HAS_CRYPTO = False


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

    def _send_text(self, receive_id_type: str, receive_id: str, text: str) -> dict:
        token = self._tenant_token()
        body = self._post(
            f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            {"receive_id": receive_id, "msg_type": "text",
             "content": json.dumps({"text": text}, ensure_ascii=False)},
            token=token)
        if body.get("code", 0) != 0:
            raise FeishuError(f"飞书发送失败 code={body.get('code')} "
                              f"msg={body.get('msg')}")
        return body

    def send_text(self, open_id: str, text: str) -> dict:
        """私聊发送（open_id）。"""
        return self._send_text("open_id", open_id, text)

    def send_text_chat(self, chat_id: str, text: str) -> dict:
        """群聊发送（chat_id，回信入群）。"""
        return self._send_text("chat_id", chat_id, text)


def _aes_cbc_decrypt(key: bytes, iv: bytes, ct: bytes) -> bytes:
    if _AES is not None:
        return _unpad(_AES.new(key, _AES.MODE_CBC, iv).decrypt(ct), 16)
    dec = _Cipher(_alg.AES(key), _modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    u = _PKCS7(128).unpadder()
    return u.update(padded) + u.finalize()


def _decrypt_event(encrypt_b64: str, encrypt_key: str) -> dict:
    """解密事件体：AES-256-CBC（key=SHA256(encrypt_key)）→ JSON dict。

    兼容两种密文格式：飞书标准（前 16 字节为随机 IV）/ 简化（IV 全 0）。
    """
    key = hashlib.sha256(encrypt_key.encode()).digest()
    raw = base64.b64decode(encrypt_b64)
    candidates = []
    if len(raw) > 16:
        candidates.append((raw[:16], raw[16:]))
    candidates.append((b"\x00" * 16, raw))
    for iv, ct in candidates:
        if not ct:
            continue
        try:
            data = json.loads(_aes_cbc_decrypt(key, iv, ct).decode("utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    raise FeishuError("解密失败：密文或 encrypt_key 不正确")


class FeishuWebhook(IMChannel):
    """飞书渠道：事件回调处理器（入站）+ 消息 API（出站）。

    实现 IMChannel 统一接口——出站 send_text/send_text_chat 转发到内部
    FeishuClient，入站走 handle。上层（notify / UrgeCenter / ChannelHub）
    只认 IMChannel，不感知飞书具体实现。
    """

    platform = "feishu"

    def __init__(self, store: JsonStore, gateway: LLMGateway | None,
                 client: FeishuClient, bindings: Bindings,
                 verification_token: str = "", default_to: str = "",
                 encrypt_key: str = "", bot_open_id: str = ""):
        self.store = store
        self.gateway = gateway
        self.client = client
        self.bindings = bindings
        self.default_to = default_to
        self._token = verification_token or getattr(client, "verification_token", "")
        self._encrypt_key = encrypt_key
        self._bot_open_id = bot_open_id or os.environ.get(
            "LAOBAN_FEISHU_BOT_OPEN_ID", "").strip()
        self._seen: deque[str] = deque(maxlen=_SEEN_CAP)
        self._seen_set: set[str] = set()

    # ---- 出站（IMChannel 接口）：转发到 FeishuClient，异常吞掉返回 False ----
    def send_text(self, im_user: str, text: str) -> bool:
        try:
            self.client.send_text(im_user, text)
            return True
        except Exception as e:
            print(f"[IM:feishu] 推送失败（{im_user}）：{e!r}")
            return False

    def send_text_chat(self, chat_id: str, text: str) -> bool:
        try:
            self.client.send_text_chat(chat_id, text)
            return True
        except Exception as e:
            print(f"[IM:feishu] 群聊推送失败（{chat_id}）：{e!r}")
            return False

    def handle(self, body: dict, background: bool = True) -> tuple[int, dict]:
        """处理一条事件 JSON，返回 (HTTP 状态码, 响应体)。"""
        if isinstance(body, dict) and body.get("encrypt"):
            if not self._encrypt_key:
                return 400, {"error": "收到加密事件但未配置 encrypt_key"
                             "（LAOBAN_FEISHU_ENCRYPT_KEY）"}
            if not _HAS_CRYPTO:
                return 500, {"error": "解密需要 pycryptodome 或 cryptography"
                             "（pip install pycryptodome）"}
            try:
                body = _decrypt_event(body["encrypt"], self._encrypt_key)
            except Exception as e:
                return 400, {"error": f"解密失败：{e}"}

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
        mentions = [m for m in (message.get("mentions", []) or [])
                    if isinstance(m, dict)]

        # 群聊：未 @机器人 → 忽略（不吵群）；@了 → 回信进群（chat_id）
        reply_chat_id = ""
        if message.get("chat_type", "p2p") == "group":
            if not self._mentioned_bot(mentions):
                return 200, {"code": 0}
            reply_chat_id = message.get("chat_id", "")

        if message.get("message_type") != "text":
            if im_user:
                self._notify(im_user, "暂仅支持文本消息")
            return 200, {"code": 0}
        try:
            text = json.loads(message.get("content", "{}")).get("text", "")
        except (json.JSONDecodeError, TypeError, AttributeError):
            text = ""
        # 剥离 @提及 token（@_user_1 等），再解析「同事id: 内容」
        for m in mentions:
            key = m.get("key", "")
            if key:
                text = text.replace(key, "")
        text = text.strip()
        if not text or not im_user:
            return 200, {"code": 0}

        work = lambda: self._process(im_user, text, reply_chat_id)   # noqa: E731
        if background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
        return 200, {"code": 0}

    def _mentioned_bot(self, mentions: list) -> bool:
        """群聊事件里是否 @了机器人。"""
        for m in mentions:
            mid = m.get("id") or {}
            if self._bot_open_id:
                if (mid.get("open_id") == self._bot_open_id
                        or mid.get("user_id") == self._bot_open_id):
                    return True
            else:
                # 未配置 bot open_id：事件能进来说明大概率 @的是机器人
                return True
        return False

    def _process(self, im_user: str, text: str, chat_id: str = "") -> None:
        try:
            if chat_id:
                reply = lambda t: self.client.send_text_chat(chat_id, t)   # noqa: E731
            else:
                reply = lambda t: self.client.send_text(im_user, t)        # noqa: E731
            # 入站处理期间落库的回信/中转由本渠道路由推送；
            # 抑制全局新消息钩子，避免同一条消息给对方推两次
            from ..core.messenger import suppress_notify
            with suppress_notify():
                result = route_inbound(self.store, self.gateway, self.bindings,
                                       "feishu", im_user, text,
                                       push=self.client.send_text,
                                       default_to=self.default_to, reply=reply)
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
