from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Message:
    role: str          # system / user / assistant / tool
    content: str
    name: str = ""


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage_tokens: int = 0    # 本次调用总 token 数（成本核算用；未知=0）


class LLMProvider(Protocol):
    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        ...
