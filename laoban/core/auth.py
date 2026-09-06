"""员工口令鉴权：PBKDF2-HMAC-SHA256 存储（{root}/auth.json）。

- set_password：随机盐 + 指定迭代次数，哈希十六进制落盘（原子写）；
- verify：常量时间比较（hmac.compare_digest）；
- enabled()：任何员工设过口令 = 鉴权启用（看板据此强制登录）；
  未设任何口令 = 本地免鉴权模式（保持向后兼容）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path

ITERATIONS = 120_000   # PBKDF2-HMAC-SHA256 迭代次数


class AuthStore:
    def __init__(self, root: str | Path):
        self.path = Path(root) / "auth.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"employees": {}}
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("employees"), dict):
            data["employees"] = {}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def set_password(self, emp_id: str, password: str) -> None:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                     bytes.fromhex(salt), ITERATIONS)
        data = self._load()
        data["employees"][emp_id] = {
            "salt": salt, "hash": digest.hex(), "iterations": ITERATIONS,
        }
        self._save(data)

    def verify(self, emp_id: str, password: str) -> bool:
        rec = self._load()["employees"].get(emp_id)
        if not rec:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(rec["salt"]),
            rec.get("iterations", ITERATIONS))
        return hmac.compare_digest(digest.hex(), rec["hash"])

    def remove(self, emp_id: str) -> bool:
        data = self._load()
        if emp_id not in data["employees"]:
            return False
        del data["employees"][emp_id]
        self._save(data)
        return True

    def enabled(self) -> bool:
        """任何员工设过口令 → 看板聊天等入口需要登录。"""
        return bool(self._load()["employees"])

    def list_accounts(self) -> list[str]:
        return list(self._load()["employees"])
