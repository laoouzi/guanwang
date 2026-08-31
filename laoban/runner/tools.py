from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    execute: Callable[[dict[str, Any]], str]


def _file_rw(args: dict[str, Any]) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    mode = args.get("mode", "write")
    if mode == "read":
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"written {len(content)} bytes"


def _shell_exec(args: dict[str, Any]) -> str:
    import subprocess
    cmd = args.get("command", "")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr
    except subprocess.TimeoutExpired:
        return "timeout"


def _web_search(args: dict[str, Any]) -> str:
    return "（v0.1 演示）搜索结果占位"


TOOLS: dict[str, Tool] = {
    "file_rw": Tool("file_rw", "读写文件", _file_rw),
    "shell_exec": Tool("shell_exec", "执行命令", _shell_exec),
    "web_search": Tool("web_search", "网页搜索", _web_search),
}
