"""Offline tests for the free-tier path: Groq/Gemini adapter + direct-Telegram reminders."""
import json

import pytest

from app import config
from app.agent import tools
from app.agent.agent import MunimAgent
from app.agent.system_prompt import TOOLS
from app.services import openai_compat_llm as oc


def test_tool_schema_translation_drops_unsupported_keys():
    out = oc.to_openai_tools(TOOLS)
    assert {t["function"]["name"] for t in out} == {t["name"] for t in TOOLS}
    for t in out:
        for prop in t["function"]["parameters"].get("properties", {}).values():
            assert "default" not in prop
    assert TOOLS[2]["input_schema"]["properties"]["days_threshold"]["default"] == 10  # original untouched


def test_message_translation_round_trip():
    history = [
        {"role": "user", "content": "Suresh ka udhaar?"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "udhaar_lookup", "input": {"customer_name": "Suresh"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": '{"found": true}'}]},
    ]
    msgs = oc.to_openai_messages("SYS", history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "udhaar_lookup", "arguments": '{"customer_name": "Suresh"}'}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"found": true}'}

    resp = oc.from_openai_message({"content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "daily_summary", "arguments": '{"date": "mangalvaar"}'}}]}, "tool_calls")
    assert resp.stop_reason == "tool_use" and resp.tool_calls[0].input == {"date": "mangalvaar"}
    bad = oc.from_openai_message({"tool_calls": [{"function": {"name": "daily_summary", "arguments": "{oops"}}]}, "tool_calls")
    assert bad.tool_calls[0].input == {} and bad.tool_calls[0].id  # malformed args don't crash, id generated


class _FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def test_agent_runs_end_to_end_on_fake_groq(monkeypatch):
    """Groq returns a tool call, then a final answer built from the tool result."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        if len(sent) == 1:
            return _FakeResp(429, {"error": {"message": "Rate limit reached. Please try again in 10ms."}})  # short wait -> retried
        if len(sent) == 2:
            return _FakeResp(200, {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "udhaar_lookup", "arguments": '{"customer_name": "Suresh"}'}}]}}]})
        tool_msg = json["messages"][-1]
        assert tool_msg["role"] == "tool" and "1850" in tool_msg["content"]
        return _FakeResp(200, {"choices": [{"finish_reason": "stop", "message": {"content": "Suresh ji ka ₹1,850 baaki hai."}}]})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)
    llm = oc.OpenAICompatLLMService(provider="groq", model="llama-3.3-70b-versatile")
    r = MunimAgent(llm=llm).ask("Suresh ka kitna udhaar hai?", session_id="g1")
    assert r.reply == "Suresh ji ka ₹1,850 baaki hai."
    assert r.tool_calls[0].name == "udhaar_lookup"
    assert sent[1]["tools"] and sent[1]["model"] == "llama-3.3-70b-versatile"


def test_missing_free_key_gives_clear_error(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        oc.OpenAICompatLLMService(provider="gemini")


def test_reminder_goes_to_telegram(telegram):
    r = tools.send_reminder("Suresh", confirmed=True)
    assert r["status"] == "sent" and r["via"] == "telegram"
    assert len(telegram) == 1 and "सुरेश" in telegram[0]["text"] and "1,850" in telegram[0]["text"]


def test_reminder_backend_failure_is_reported_not_raised(monkeypatch):
    class Boom:
        def send_message(self, *a, **k):
            raise ConnectionError("no internet")
    monkeypatch.setattr("app.services.telegram_service.get_telegram_service", lambda: Boom())
    r = tools.send_reminder("Suresh", confirmed=True)
    assert r["status"] == "error" and r["sent"] is False


def test_parse_retry_wait():
    assert oc.parse_retry_wait(_FakeResp(429, {"m": "Please try again in 592.5ms."})) == 0.5925
    assert oc.parse_retry_wait(_FakeResp(429, {"m": "try again in 1m2.5s"})) == 62.5
    assert oc.parse_retry_wait(_FakeResp(429, {"m": "nope"})) is None


def test_history_window_keeps_last_turns_intact():
    msgs = []
    for i in range(6):
        msgs += [{"role": "user", "content": f"q{i}"},
                 {"role": "assistant", "content": [{"type": "tool_use", "id": f"c{i}", "name": "x", "input": {}}]},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"c{i}", "content": "{}"}]},
                 {"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]}]
    w = oc.window_history(msgs, 4)
    assert w[0] == {"role": "user", "content": "q2"} and len(w) == 16
    assert oc.window_history(msgs[:8], 4) == msgs[:8]


def test_rate_limited_model_falls_back_to_next(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(config, "LLM_FALLBACK_MODELS", ["backup-model"])
    used = []

    def fake_post(url, headers=None, json=None, timeout=None):
        used.append(json["model"])
        if json["model"] == "main-model":
            return _FakeResp(429, {"error": {"message": "try again in 40s"}})
        return _FakeResp(200, {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    llm = oc.OpenAICompatLLMService(provider="groq", model="main-model")
    assert llm.chat("s", [{"role": "user", "content": "hi"}], []).text == "ok"
    assert used == ["main-model", "backup-model"] and llm.last_model_used == "backup-model"


LEAKED = ("Telegram ke liye reminder ************...\n\n\n\nSorry...\n\n\n\nOops!\n\n\n\nWe have to follow rules: tool "
          "returned not_configured, so must tell reason in one sentence. Reply in Hinglish.Telegram abhi set up nahi hai, "
          "isliye reminder nahi bheja ja sakta.")


def test_leaked_reasoning_is_detected_and_cleaned():
    assert oc.looks_leaked(LEAKED)
    assert oc.clean_reply(LEAKED) == "Telegram abhi set up nahi hai, isliye reminder nahi bheja ja sakta."
    for ok in ("Suresh ji ka ₹1,850 udhaar baaki hai. Reminder bhej doon?", "सुरेश जी का ₹1,850 उधार बाकी है।"):
        assert not oc.looks_leaked(ok)


def test_leaked_reply_is_retried_then_cleaned(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    replies = iter([LEAKED, "Telegram abhi set up nahi hai."])

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResp(200, {"choices": [{"finish_reason": "stop", "message": {"content": next(replies)}}]})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    llm = oc.OpenAICompatLLMService(provider="groq", model="m")
    assert llm.chat("s", [{"role": "user", "content": "hi"}], []).text == "Telegram abhi set up nahi hai."


def test_history_compaction_keeps_recent_tool_context_only():
    msgs = []
    for i in range(4):
        msgs += [{"role": "user", "content": f"q{i}"},
                 {"role": "assistant", "content": [{"type": "tool_use", "id": f"c{i}", "name": "x", "input": {}}]},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"c{i}", "content": "{...}"}]},
                 {"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]}]
    out = oc.compact_history(msgs, turns=4)
    assert out[:4] == [{"role": "user", "content": "q0"}, {"role": "assistant", "content": [{"type": "text", "text": "a0"}]},
                       {"role": "user", "content": "q1"}, {"role": "assistant", "content": [{"type": "text", "text": "a1"}]}]
    assert out[4:] == msgs[8:]   # previous + current turn keep full tool calls/results


def test_repeated_sentences_are_removed():
    dup = "Telegram abhi set up nahi hai, isliye reminder bhejna possible nahi hai.Telegram abhi set up nahi hai, isliye reminder bhejna possible nahi hai."
    assert oc.dedupe_sentences(dup) == "Telegram abhi set up nahi hai, isliye reminder bhejna possible nahi hai."
    ok = "Suresh ji ka ₹1,850 baaki hai. Reminder bhej doon?"
    assert oc.dedupe_sentences(ok) == ok
