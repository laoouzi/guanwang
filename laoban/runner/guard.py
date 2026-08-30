from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Guard:
    """合规硬规则层：确定性执行，不依赖 LLM。"""

    blocklist: list[str] = field(default_factory=lambda: [
        "rm -rf", "mkfs", "dd if=", "curl", "| sh", "| bash",
    ])
    domain_allowlist: list[str] = field(default_factory=list)

    def check_command(self, cmd: str) -> str:
        low = cmd.lower()
        if any(b in low for b in self.blocklist):
            return "high"
        return "high"  # 命令执行一律 high（安全优先）

    def check_url(self, url: str) -> str:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        if any(host == d or host.endswith("." + d) for d in self.domain_allowlist):
            return "medium"
        return "high"


def classify_risk(tool: str, args: dict) -> str:
    """根据工具 + 参数判定风险等级 low/medium/high。"""
    if tool == "file_rw":
        path = str(args.get("path", ""))
        if path.startswith("workspaces/"):
            return "low"
        return "high"
    if tool == "web_search":
        url = str(args.get("url", ""))
        from urllib.parse import urlparse
        if urlparse(url).netloc:
            return "medium"
        return "low"
    if tool == "shell_exec":
        return "high"
    return "low"
