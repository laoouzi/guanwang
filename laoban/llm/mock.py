from __future__ import annotations

from .base import LLMResponse, Message


class MockLLM:
    """演示模式 LLM：按脚本循环返回，承诺永不抛错。"""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ["（演示模式）任务已处理。"]
        self._idx = 0

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        r = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return LLMResponse(content=r)
