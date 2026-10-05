"""LLM service: free Groq/Gemini (see openai_compat_llm.py) or Claude with tool use.

All return an LLMResponse. `content` is appended to the conversation history as-is
(Claude: SDK content blocks, so thinking blocks round-trip unchanged; Groq/Gemini: plain dicts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app import config


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    content: list[Any]                     # assistant content blocks for history
    text: str = ""                         # concatenated text blocks
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"


def _get(block: Any, key: str, default=None):
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def parse_blocks(content: list[Any], stop_reason: str) -> LLMResponse:
    text = " ".join(_get(b, "text", "") for b in content if _get(b, "type") == "text").strip()
    calls = [ToolCall(_get(b, "id"), _get(b, "name"), dict(_get(b, "input") or {}))
             for b in content if _get(b, "type") == "tool_use"]
    return LLMResponse(content=list(content), text=text, tool_calls=calls, stop_reason=stop_reason)


class LLMService:
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self) -> None:
        import anthropic

        # Reads ANTHROPIC_API_KEY from env if not passed; never hardcode keys.
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
        self.model = config.LLM_MODEL

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs = dict(
            model=self.model,
            max_tokens=config.LLM_MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": config.LLM_EFFORT},  # low effort => snappy voice replies
        )
        if config.LLM_USE_FALLBACKS:
            # Server-side fallback: if the model declines, Anthropic re-runs on a recommended model.
            resp = self.client.beta.messages.create(**kwargs, betas=[self.FALLBACK_BETA], fallbacks="default")
        else:
            resp = self.client.messages.create(**kwargs)

        if resp.stop_reason == "refusal":
            return LLMResponse(content=list(resp.content), text="Maaf kijiye, is sawaal mein main madad nahi kar paunga.",
                               stop_reason="refusal")
        return parse_blocks(resp.content, resp.stop_reason)


import logging
logger = logging.getLogger(__name__)


class ChainLLMService:
    """Try each provider in turn (e.g. Groq, then Gemini) when the previous one is rate-limited or down."""

    def __init__(self, services: list) -> None:
        self.services = services
        self.provider = "+".join(sv.provider for sv in services)
        self.model = services[0].model
        self.last_model_used = None

    def chat(self, system: str, messages: list[dict], tools: list[dict]):
        import requests
        last: Exception | None = None
        for sv in self.services:
            try:
                resp = sv.chat(system, messages, tools)
                self.last_model_used = f"{sv.provider}:{sv.last_model_used or sv.model}"
                return resp
            except (RuntimeError, requests.RequestException) as e:
                last = e
                logger.warning("provider %s unavailable, trying next: %s", sv.provider, str(e)[:160])
        raise RuntimeError(f"all AI providers failed: {last}")


def get_llm_service():
    """LLM_PROVIDER: groq | gemini (free tiers) | anthropic."""
    if config.LLM_PROVIDER in ("groq", "gemini"):
        from app.services.openai_compat_llm import OpenAICompatLLMService
        primary = OpenAICompatLLMService()
        if config.LLM_PROVIDER == "groq" and config.GEMINI_API_KEY:
            # Second free provider with separate limits: used only when every Groq model is rate-limited.
            backup = OpenAICompatLLMService("gemini", config.GEMINI_BACKUP_MODELS[0])
            backup.fallback_models = config.GEMINI_BACKUP_MODELS[1:]
            return ChainLLMService([primary, backup])
        return primary
    if config.LLM_PROVIDER == "anthropic":
        return LLMService()
    raise ValueError(f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}' (use groq, gemini or anthropic)")
