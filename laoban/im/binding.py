"""IM 绑定表：IM 账号 ↔ 员工 id（渠道入口的身份映射）。

存储：{root}/im_bindings.json（原子写，与 store 同风格）。
绑定由管理员通过 CLI 维护：laoban im bind --platform feishu --im-user <open_id> --employee <id>
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class Bindings:
    def __init__(self, root: str | Path):
        self.path = Path(root) / "im_bindings.json"

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("bindings", []) if isinstance(data, dict) else data
        return [b for b in items if isinstance(b, dict)]

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"bindings": items}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def bind(self, platform: str, im_user: str, employee: str) -> dict:
        """绑定（同 (platform, im_user) 重复绑定则覆盖）。"""
        items = self._load()
        for b in items:
            if b.get("platform") == platform and b.get("im_user") == im_user:
                b["employee"] = employee
                self._save(items)
                return b
        item = {"platform": platform, "im_user": im_user, "employee": employee}
        items.append(item)
        self._save(items)
        return item

    def unbind(self, platform: str, im_user: str) -> bool:
        items = self._load()
        rest = [b for b in items
                if not (b.get("platform") == platform and b.get("im_user") == im_user)]
        if len(rest) == len(items):
            return False
        self._save(rest)
        return True

    def lookup(self, platform: str, im_user: str) -> str | None:
        """IM 账号 → 员工 id。"""
        for b in self._load():
            if b.get("platform") == platform and b.get("im_user") == im_user:
                return b.get("employee")
        return None

    def lookup_by_employee(self, platform: str, employee: str) -> str | None:
        """员工 id → IM 账号（出站推送用）。"""
        for b in self._load():
            if b.get("platform") == platform and b.get("employee") == employee:
                return b.get("im_user")
        return None

    def list(self) -> list[dict]:
        return self._load()
