"""Free-tier LLMs (Groq, Gemini) via their OpenAI-compatible /chat/completions endpoints.

The agent keeps its history in Anthropic block format (text / tool_use / tool_result). This
adapter translates that to OpenAI chat format on the way out, and translates the reply back to
the same block format, so agent.py, the tools and the reminder guard work unchanged.
Uses plain `requests` — no extra SDK.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
import uuid

import requests

from app import config
from app.services.llm_service import LLMResponse, parse_blocks

logger = logging.getLogger(__name__)

PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key": lambda: config.GROQ_API_KEY, "env": "GROQ_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key": lambda: config.GEMINI_API_KEY, "env": "GEMINI_API_KEY"},
}


# ------------------------------------------------------------------ translation
def _clean_schema(schema: dict) -> dict:
    """Drop JSON-schema keys some providers reject (e.g. Gemini rejects `default`)."""
    s = copy.deepcopy(schema)
    for prop in s.get("properties", {}).values():
        prop.pop("default", None)
    if not s.get("required"):
        s.pop("required", None)
    return s


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": _clean_schema(t["input_schema"])}} for t in tools]


# Gemini 3 models require each earlier tool call's "thought signature" to be sent back. Calls made by another
# model (e.g. Groq) have none; Google documents this placeholder for that case.
_GEMINI_SKIP_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def to_openai_messages(system: str, messages: list[dict], gemini: bool = False) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "user":
            texts = []
            for b in content:
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                "content": c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)})
                elif b.get("type") == "text":
                    texts.append(b["text"])
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
        else:  # assistant
            text = " ".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)},
                      **({"extra_content": b.get("extra_content") or _GEMINI_SKIP_SIGNATURE} if gemini else {})}
                     for b in content if b.get("type") == "tool_use"]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            elif not text:
                msg["content"] = "..."
            out.append(msg)
    return out


def from_openai_message(msg: dict, finish_reason: str) -> LLMResponse:
    blocks: list[dict] = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"].strip()})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        block = {"type": "tool_use", "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}", "name": fn.get("name"), "input": args}
        if tc.get("extra_content"):
            block["extra_content"] = tc["extra_content"]      # Gemini thought signature, returned on the next call
        blocks.append(block)
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return parse_blocks(blocks, stop)


# ------------------------------------------------------------------ service
class OpenAICompatLLMService:
    def __init__(self, provider: str | None = None, model: str | None = None) -> None:
        self.provider = (provider or config.LLM_PROVIDER).lower()
        if self.provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{self.provider}' (use groq or gemini)")
        p = PROVIDERS[self.provider]
        self.api_key = p["key"]()
        if not self.api_key:
            raise RuntimeError(f"{p['env']} is not set in .env (or set LLM_PROVIDER=mock)")
        self.url = f"{p['base_url']}/chat/completions"
        self.model = model or config.LLM_MODEL

        self.fallback_models = [m for m in config.LLM_FALLBACK_MODELS if m != self.model]
        self.last_model_used = None

    def _body(self, model: str, system: str, messages: list[dict], tools: list[dict]) -> dict:
        body = {
            "model": model,
            "messages": to_openai_messages(system, compact_history(messages, config.LLM_HISTORY_TURNS),
                                           gemini=self.provider == "gemini"),
            "temperature": config.LLM_TEMPERATURE,
            "max_tokens": config.LLM_MAX_TOKENS,
        }
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"  # fewer hidden reasoning tokens -> faster + less of the TPM budget
        elif model.startswith("gemini-2.5"):
            body["reasoning_effort"] = "low"  # Gemini thinks by default; keep it short so the reply fits
        if tools:
            body["tools"] = to_openai_tools(tools)
            body["tool_choice"] = "auto"
        return body

    def _complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        """One completion, walking the model fallback chain on rate limits."""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_err, last_wait = "", None
        # Free-tier limits are per model: on a rate limit, move to the next model instead of failing.
        for model in [self.model, *self.fallback_models]:
            body = self._body(model, system, messages, tools)
            for _attempt in range(2):
                resp = requests.post(self.url, headers=headers, json=body, timeout=45)
                if resp.status_code == 200:
                    self.last_model_used = model
                    choice = resp.json()["choices"][0]
                    return from_openai_message(choice.get("message") or {}, choice.get("finish_reason", ""))
                last_err = f"{resp.status_code}: {resp.text[:300]}"
                if resp.status_code == 429:
                    last_wait = parse_retry_wait(resp)
                    if last_wait is not None and last_wait <= 3:
                        time.sleep(last_wait + 0.2)  # short wait on the same model
                        continue
                    break  # long wait -> next model
                if resp.status_code >= 500 or "tool_use_failed" in resp.text:
                    time.sleep(1)  # transient / malformed tool call -> one retry
                    continue
                if resp.status_code == 400 and "reasoning_effort" in resp.text:
                    body.pop("reasoning_effort", None)
                    continue
                if resp.status_code in (404, 400) and "model" in resp.text.lower():
                    break  # model not available on this account -> next model
                raise RuntimeError(f"{self.provider} API error {last_err}")
        # Every model is rate-limited: wait once (up to 25s) for the primary to free up.
        if last_wait is not None and last_wait <= 25:
            time.sleep(last_wait + 0.3)
            resp = requests.post(self.url, headers=headers, json=self._body(self.model, system, messages, tools), timeout=45)
            if resp.status_code == 200:
                self.last_model_used = self.model
                choice = resp.json()["choices"][0]
                return from_openai_message(choice.get("message") or {}, choice.get("finish_reason", ""))
            last_err = f"{resp.status_code}: {resp.text[:300]}"
        raise RuntimeError(f"{self.provider} API error {last_err}")

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        resp = self._complete(system, messages, tools)
        if resp.text and looks_leaked(resp.text):
            # Some free models occasionally write their reasoning into the answer. Ask once more,
            # then clean whatever is left so the merchant never hears it.
            logger.warning("reasoning leaked into reply; retrying", extra={"model": self.last_model_used})
            resp = self._complete(system, messages, tools)
            if resp.text and looks_leaked(resp.text):
                resp = _with_text(resp, clean_reply(resp.text) or "माफ़ कीजिए, एक बार फिर पूछिए?")
        if resp.text:
            deduped = dedupe_sentences(resp.text)
            if deduped != resp.text:
                resp = _with_text(resp, deduped)
        return resp


# ------------------------------------------------------------------ leaked-reasoning guard
_META = re.compile(
    r"\b(we (have|need|should|must) to|the user (asked|asks|wants|said|says|is asking)|tool returned|"
    r"must (tell|reply|respond|answer|say)|let'?s (respond|answer|reply|craft|produce)|"
    r"reply (in|with) (hinglish|hindi|marathi|english)|according to (the )?(rules|instructions|system)|"
    r"in one sentence|so (we|i) (should|must|need)|the assistant should|as per (the )?instructions)\b", re.I)
_JUNK = re.compile(r"\*{4,}|(?:\n\s*){4,}|<think>", re.I)


def looks_leaked(text: str) -> bool:
    return bool(_META.search(text) or _JUNK.search(text))


def clean_reply(text: str) -> str:
    """Keep the final run of real spoken sentences; drop reasoning, filler and junk."""
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S | re.I)
    parts = [p.strip() for p in re.split(r"(?<=[.!?।])\s*|\n+", text) if p and p.strip()]
    kept: list[str] = []
    for p in parts:
        letters = re.sub(r"[^\wऀ-ॿ]", "", p)
        junk = (_META.search(p) or "***" in p or len(letters) < 3
                or re.fullmatch(r"(sorry|oops|hmm|ok)\W*", p, re.I))
        if junk:
            kept = []          # anything before reasoning/junk is discarded; the answer comes last
        else:
            kept.append(p)
    return " ".join(kept).strip()


def dedupe_sentences(text: str) -> str:
    """Drop a sentence that repeats the one before it (free models sometimes echo their answer)."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?।])\s*", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if not out or re.sub(r"\W", "", p).lower() != re.sub(r"\W", "", out[-1]).lower():
            out.append(p)
    return " ".join(out) if len(out) < len(parts) else text


def _with_text(resp: LLMResponse, text: str) -> LLMResponse:
    blocks = [b for b in resp.content if not (isinstance(b, dict) and b.get("type") == "text")]
    return parse_blocks([{"type": "text", "text": text}, *blocks], resp.stop_reason)


def compact_history(messages: list[dict], turns: int, full_turns: int = 2) -> list[dict]:
    """Keep the last `turns` merchant turns. The newest `full_turns` (current + previous, which a
    confirmation needs) keep their tool calls/results; older turns shrink to question + final answer,
    so each request stays small enough for free-tier token limits."""
    def starts_of(msgs):
        return [i for i, m in enumerate(msgs) if m["role"] == "user" and isinstance(m["content"], str)]

    starts = starts_of(messages)
    if turns > 0 and len(starts) > turns:
        messages = messages[starts[-turns]:]
        starts = starts_of(messages)
    cut = starts[-full_turns] if len(starts) > full_turns else 0
    out: list[dict] = []
    for m in messages[:cut]:
        if m["role"] == "user" and isinstance(m["content"], str):
            out.append(m)
        elif m["role"] == "assistant" and isinstance(m["content"], list):
            text = " ".join(b.get("text", "") for b in m["content"] if b.get("type") == "text").strip()
            if text and not any(b.get("type") == "tool_use" for b in m["content"]):
                out.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
    return out + messages[cut:]


def window_history(messages: list[dict], turns: int) -> list[dict]:
    """Keep only the last `turns` merchant turns (a turn starts at a plain-text user message)."""
    starts = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)]
    if turns <= 0 or len(starts) <= turns:
        return messages
    return messages[starts[-turns]:]


def parse_retry_wait(resp) -> float | None:
    """Seconds to wait from a 429: retry-after header or 'try again in 1m2.5s / 592.5ms'."""
    ra = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", resp.text or "")
    if not m:
        return None
    secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return secs + 60 * int(m.group(1) or 0)
