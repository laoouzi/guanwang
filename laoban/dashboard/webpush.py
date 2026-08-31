"""Web Push 离线通知：VAPID + 订阅管理 + aes128gcm 加密推送。

让收件人即使不看看板（浏览器标签关闭 / 手机锁屏）也能收到新消息推送，
补上「必须开着看板才看得到红点」的触达短板——真正替代 IM 触达的兜底。

协议：RFC 8291（VAPID 鉴权）+ RFC 8188（aes128gcm 内容编码）。
加密依赖 cryptography（与飞书事件加密同一可选依赖）；缺库时静默降级为
「订阅照收、推送跳过」，绝不让推送失败炸掉看板。

存储：
  {root}/vapid.json       VAPID P-256 密钥对（首次启动生成，长期复用）
  {root}/webpush_subs.json  订阅表 [{employee, endpoint, p256dh, auth}]

aes128gcm 加密要点：
  salt = 16 随机字节（进头部）；rs = 4096；idlen = 0
  prk   = HKDF-Extract(salt=auth_secret, ikm=ECDH_shared)
  cek   = HKDF-Expand(prk, "WebPush: info\\0" + ua_pub + local_pub, 16)
  nonce = HKDF-Expand(prk, "WebPush: nonce\\0" + ua_pub + local_pub, 12)
  header = salt + rs + idlen；密文 = AESGCM(cek).encrypt(nonce, payload, aad=header)
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
import urllib.parse
import urllib.request

from ..core.store import JsonStore

# 可选加密库（cryptography）；缺失时 _HAS_CRYPTO=False，推送静默跳过
try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    _HAS_CRYPTO = True
except ImportError:   # pragma: no cover - 依赖缺失走降级
    _HAS_CRYPTO = False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(s) -> bytes:
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class WebPushManager:
    """订阅管理与推送：新消息 → 收件人已订阅的浏览器弹系统通知。"""

    def __init__(self, store: JsonStore, subject: str = "mailto:laoban@localhost",
                 ttl: int = 3600, timeout: int = 15):
        self.store = store
        self.subject = subject
        self.ttl = max(1, int(ttl))
        self.timeout = int(timeout)
        self._subs_path = store.root / "webpush_subs.json"
        self._vapid_path = store.root / "vapid.json"
        self.public_key, self._private_key = self._load_or_create_vapid()

    @property
    def enabled(self) -> bool:
        return _HAS_CRYPTO and bool(self.public_key)

    # ---- VAPID 密钥 ----
    def _load_or_create_vapid(self):
        """读已有密钥对；首次生成并落盘。返回 (public_b64url, private_obj)。"""
        data = self.store._read_json(self._vapid_path) or {}
        pub = data.get("public", "")
        priv = data.get("private", "")
        if not (pub and priv) or not _HAS_CRYPTO:
            if _HAS_CRYPTO:
                key = ec.generate_private_key(ec.SECP256R1())
                priv_raw = key.private_numbers().private_value.to_bytes(32, "big")
                pub_raw = key.public_key().public_bytes(
                    Encoding.X962, PublicFormat.UncompressedPoint)
                pub, priv = _b64url(pub_raw), _b64url(priv_raw)
                self.store._atomic_write(
                    self._vapid_path, {"public": pub, "private": priv})
            else:
                return "", None
        if not _HAS_CRYPTO:
            return pub, None
        key = ec.derive_private_key(
            int.from_bytes(_unb64url(priv), "big"), ec.SECP256R1())
        return pub, key

    def _vapid_jwt(self, aud: str) -> str:
        header = _b64url(json.dumps({"typ": "JWT", "alg": "ES256"},
                                    separators=(",", ":")).encode())
        claims = _b64url(json.dumps({
            "aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": self.subject,
        }, separators=(",", ":")).encode())
        signing_input = f"{header}.{claims}".encode()
        sig = self._private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(sig)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{header}.{claims}.{_b64url(raw)}"

    # ---- 订阅表 ----
    def _load_subs(self) -> list[dict]:
        data = self.store._read_json(self._subs_path) or {}
        subs = data.get("subscriptions", []) if isinstance(data, dict) else data
        return [s for s in subs if isinstance(s, dict)
                and s.get("endpoint") and s.get("p256dh") and s.get("auth")]

    def _save_subs(self, subs: list[dict]) -> None:
        self.store._atomic_write(self._subs_path, {"subscriptions": subs})

    def subscribe(self, employee: str, endpoint: str, p256dh: str, auth: str) -> dict:
        subs = self._load_subs()
        for s in subs:
            if s.get("employee") == employee and s.get("endpoint") == endpoint:
                s["p256dh"], s["auth"] = p256dh, auth
                self._save_subs(subs)
                return s
        item = {"employee": employee, "endpoint": endpoint,
                "p256dh": p256dh, "auth": auth}
        subs.append(item)
        self._save_subs(subs)
        return item

    def unsubscribe(self, employee: str, endpoint: str) -> bool:
        subs = self._load_subs()
        rest = [s for s in subs if not (s.get("employee") == employee
                                        and s.get("endpoint") == endpoint)]
        if len(rest) == len(subs):
            return False
        self._save_subs(rest)
        return True

    def subscriptions(self, employee: str | None = None) -> list[dict]:
        subs = self._load_subs()
        return [s for s in subs
                if employee is None or s.get("employee") == employee]

    # ---- 推送 ----
    def notify(self, employee_id: str, title: str, body: str,
               url: str = "/") -> int:
        """给员工所有已订阅设备推一条系统通知，返回成功推出去的条数。

        无订阅 / 缺加密库 / 推送失败都静默（打印不抛）——通知尽力而为，
        消息总线才是唯一事实源。
        """
        if not self.enabled:
            return 0
        payload = json.dumps({"title": title, "body": body, "url": url},
                             ensure_ascii=False).encode()
        ok = 0
        for sub in self.subscriptions(employee_id):
            try:
                self._send_one(sub, payload)
                ok += 1
            except Exception as e:
                print(f"[webpush] 推送失败（{employee_id}）：{e!r}")
        return ok

    def _send_one(self, sub: dict, payload: bytes) -> None:
        body = self._encrypt_aes128gcm(sub, payload)
        aud = urllib.parse.urlparse(sub["endpoint"])
        aud = f"{aud.scheme}://{aud.netloc}"
        req = urllib.request.Request(sub["endpoint"], data=body, method="POST")
        req.add_header("Authorization",
                       f"vapid t={self._vapid_jwt(aud)}, k={self.public_key}")
        req.add_header("Content-Encoding", "aes128gcm")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("TTL", str(self.ttl))
        with urllib.request.urlopen(req, timeout=self.timeout):
            pass

    def _encrypt_aes128gcm(self, sub: dict, payload: bytes) -> bytes:
        """按 RFC 8188 aes128gcm + RFC 8291（对齐 http_ece 参考实现）加密。

        单记录（载荷远小于 4096）：
          salt = 16 随机字节；rs = 4096；idlen = 65；keyid = 临时公钥(65)
          context = "WebPush: info\\0" + client_pub + ephemeral_pub
          secret  = HKDF(SHA256, 32, salt=auth, info=context).derive(ECDH)
          cek     = HKDF(SHA256, 16, salt=salt, info="Content-Encoding: aes128gcm\\0").derive(secret)
          nonce   = HKDF(SHA256, 12, salt=salt, info="Content-Encoding: nonce\\0").derive(secret)
          明文 = payload + b"\\x02"（末记录分隔符）；密文 = AESGCM(cek).encrypt(nonce, 明文)
          最终 = salt + rs + idlen + keyid + 密文 + tag
        """
        ua_pub = _unb64url(sub["p256dh"])
        auth = _unb64url(sub["auth"])
        client_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), ua_pub)
        local_key = ec.generate_private_key(ec.SECP256R1())
        local_pub = local_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        ecdh_secret = local_key.exchange(ec.ECDH(), client_key)

        context = b"WebPush: info\x00" + ua_pub + local_pub
        secret = HKDF(algorithm=hashes.SHA256(), length=32,
                      salt=auth, info=context).derive(ecdh_secret)
        salt = os.urandom(16)
        cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
                   info=b"Content-Encoding: aes128gcm\x00").derive(secret)
        nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                     info=b"Content-Encoding: nonce\x00").derive(secret)

        # 末记录分隔符 \x02 追加到明文，AES-GCM 无 AAD
        encrypted = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
        header = salt + struct.pack("!I", 4096) + struct.pack("!B", 65) + local_pub
        return header + encrypted
