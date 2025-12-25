from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from openai import OpenAI


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 700


def load_config_from_env() -> LLMConfig:
    # LM Studio default OpenAI-compatible server is often http://localhost:1234/v1
    base_url = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1").strip()
    api_key = os.getenv("LMSTUDIO_API_KEY", "lm-studio").strip()
    model = os.getenv("LMSTUDIO_MODEL", "mistralai/ministral-3-14b-reasoning").strip()
    return LLMConfig(base_url=base_url, api_key=api_key, model=model)


def make_client(cfg: LLMConfig) -> OpenAI:
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


def extract_text(message: Any) -> str:
    # openai>=1.x returns message.content as str | list depending on modalities.
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    # Best-effort for list content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def chat_once(
    client: OpenAI,
    cfg: LLMConfig,
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return client.chat.completions.create(**kwargs)