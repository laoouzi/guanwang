from __future__ import annotations

from typing import Any

from .base import LLMResponse, Message
from .mock import MockLLM


class LLMGateway:
    """统一 LLM 网关：按 provider 路由到具体实现。

    v0.1 只注册 mock provider；openai 兼容 HTTP provider 由后续接入，
    但路由接口已就位（provider 名 → chat 调用），不绑定具体厂商。
    """

    def __init__(self):
        self._providers: dict[str, Any] = {}

    def register_mock(self, name: str, llm: MockLLM) -> None:
        self._providers[name] = llm

    def register_provider(self, name: str, llm: Any) -> None:
        """注册任意 LLMProvider 协议实现（MockLLM / OpenAICompatibleProvider / ...）。"""
        self._providers[name] = llm

    def list_providers(self) -> list[str]:
        return list(self._providers)

    def get_provider(self, name: str):
        """按名取 provider（未注册返回 None）。"""
        return self._providers.get(name)

    def chat(self, provider: str, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        if provider not in self._providers:
            raise KeyError(f"未注册的 provider: {provider}")
        return self._providers[provider].chat(messages, tools)

    def chat_for_employee(self, model_config: dict[str, Any], messages: list[Message]) -> LLMResponse:
        return self.chat(model_config.get("provider", "mock"), messages)
