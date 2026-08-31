"""OpenAI 兼容 HTTP Provider：真实 LLM 传输层（零第三方依赖，urllib 实现）。

适用于所有 OpenAI 协议兼容服务：
  - DeepSeek     https://api.deepseek.com/v1
  - 通义千问      https://dashscope.aliyuncs.com/compatible-mode/v1
  - OpenAI       https://api.openai.com/v1
  - Kimi/Moonshot https://api.moonshot.cn/v1（kimi-k2.6 等）
  - Ollama       http://127.0.0.1:11434/v1（本地，无需 Key）

环境变量自动发现（register_from_env）：
  LAOBAN_DEEPSEEK_API_KEY    → provider "deepseek"
  LAOBAN_DASHSCOPE_API_KEY   → provider "qwen"
  LAOBAN_OPENAI_API_KEY      → provider "openai"
  LAOBAN_MOONSHOT_API_KEY    → provider "kimi"
  LAOBAN_OLLAMA_BASE_URL     → provider "ollama"
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import LLMResponse, Message

DEFAULT_TIMEOUT_SEC = 120

# 环境变量 → (provider 名, 默认 base_url, 默认 model, 是否需要 Key)
_ENV_MAP: dict[str, tuple[str, str, str, bool]] = {
    "LAOBAN_DEEPSEEK_API_KEY": (
        "deepseek", "https://api.deepseek.com/v1", "deepseek-chat", True),
    "LAOBAN_DASHSCOPE_API_KEY": (
        "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus", True),
    "LAOBAN_OPENAI_API_KEY": (
        "openai", "https://api.openai.com/v1", "gpt-4o-mini", True),
    "LAOBAN_MOONSHOT_API_KEY": (
        "kimi", "https://api.moonshot.cn/v1", "kimi-k2.6", True),
    "LAOBAN_OLLAMA_BASE_URL": (
        "ollama", "http://127.0.0.1:11434/v1", "qwen2.5:7b", False),
}


class ProviderError(Exception):
    """Provider 调用失败（网络 / 鉴权 / 服务端错误）。"""


class OpenAICompatibleProvider:
    """POST {base_url}/chat/completions，解析 choices[0].message.content。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 default_base: str = "", timeout: int = DEFAULT_TIMEOUT_SEC):
        self.base_url = (base_url or default_base).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            payload["tools"] = tools
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            raise ProviderError(
                f"LLM 服务返回 {e.code}：{detail or e.reason}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ProviderError(f"LLM 服务不可达（{self.base_url}）：{e}") from e

        try:
            choice = body["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError) as e:
            raise ProviderError(f"LLM 响应缺少 choices：{body}") from e

        # token 用量（OpenAI 协议 usage.total_tokens；缺失时按字符估算兜底）
        usage = body.get("usage") or {}
        usage_tokens = int(usage.get("total_tokens", 0) or 0)
        if not usage_tokens:
            usage_tokens = (len(payload) + len(str(msg.get("content", "")))) // 4

        return LLMResponse(
            content=msg.get("content", ""),
            tool_calls=msg.get("tool_calls", []) or [],
            usage_tokens=usage_tokens,
        )


def register_from_env(gateway) -> list[str]:
    """扫描 LAOBAN_* 环境变量，把配了 Key 的服务注册进网关。

    返回注册成功的 provider 名列表（无任何配置时为空）。
    """
    from .gateway import LLMGateway  # 局部导入避免环

    registered: list[str] = []
    for env_key, (name, base, model, need_key) in _ENV_MAP.items():
        val = os.environ.get(env_key, "").strip()
        if not val:
            continue
        if need_key:
            provider = OpenAICompatibleProvider(base_url=base, api_key=val, model=model)
        else:
            # OLLAMA_BASE_URL：值本身就是 base_url，Key 可为空
            provider = OpenAICompatibleProvider(
                base_url=val, api_key="ollama", model=model)
        gateway.register_provider(name, provider)
        registered.append(name)
    return registered
