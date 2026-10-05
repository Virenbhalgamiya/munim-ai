"""End-to-end agent flows (mock LLM by default; MUNIM_TEST_REAL=1 for real Claude)."""
from app.agent.agent import MunimAgent
from app.services.llm_service import parse_blocks


def _calls(result, name):
    return [t for t in result.tool_calls if t.name == name]


def test_tuesday_summary_mentions_dip(agent):
    r = agent.ask("Mangalvaar ko dhanda kaisa gaya?", session_id="t1")
    calls = _calls(r, "daily_summary")
    assert calls and calls[0].output["weekday"] == "Tuesday"
    reply = r.reply.lower()
    assert any(w in reply for w in ("mangal", "tuesday", "मंगल"))
    assert any(w in reply for w in ("kam", "dheema", "कम", "%", "below", "slow"))


def test_today_summary_gives_numbers_and_one_insight(agent):
    r = agent.ask("Aaj ka hisaab kaisa raha?", session_id="t2")
    out = _calls(r, "daily_summary")[0].output
    assert out["is_today"]
    assert f"{out['total_sales']:,}" in r.reply or str(out["total_sales"]) in r.reply


def test_udhaar_lookup_suresh(agent):
    r = agent.ask("Suresh ka kitna udhaar baaki hai?", session_id="t3")
    calls = _calls(r, "udhaar_lookup")
    assert calls and calls[0].output["customers"][0]["outstanding"] == 1850
    assert "1,850" in r.reply or "1850" in r.reply


def test_lapsed_customers(agent):
    r = agent.ask("Kaun se purane customer aana band ho gaye?", session_id="t4")
    assert _calls(r, "lapsed_customers")
    assert sum(n in r.reply for n in ("Anita", "Vikram", "Kavita")) >= 2


def test_confirm_then_send_reminder(agent, telegram):
    sid = "t5"
    first = agent.ask("Suresh ko payment ka reminder bhej do", session_id=sid)
    assert not any(t.input.get("confirmed") and t.output.get("status") == "sent" for t in _calls(first, "send_reminder"))
    assert not any(t.input.get("confirmed") for t in _calls(first, "send_reminder"))
    assert telegram == []
    assert "?" in first.reply  # asks for confirmation

    second = agent.ask("Haan, bhej do", session_id=sid)
    sent = [t for t in _calls(second, "send_reminder") if t.input.get("confirmed")]
    assert sent and sent[0].output["status"] == "sent"
    assert len(telegram) == 1 and "सुरेश" in telegram[0]["text"] and "1,850" in telegram[0]["text"]


def test_reminder_declined(agent, telegram):
    agent.ask("Suresh ko reminder bhej do", session_id="t6")
    r = agent.ask("Nahi, abhi mat bhejo", session_id="t6")
    assert not any(t.input.get("confirmed") for t in _calls(r, "send_reminder"))
    assert telegram == []


class _EagerLLM:
    """Misbehaving LLM that tries to send with confirmed=true in the very first turn."""

    def __init__(self):
        self.n = 0

    def chat(self, system, messages, tools):
        self.n += 1
        if self.n == 1:
            return parse_blocks([{"type": "tool_use", "id": "toolu_x1", "name": "send_reminder",
                                  "input": {"customer_name": "Suresh", "confirmed": True}}], "tool_use")
        return parse_blocks([{"type": "text", "text": "Bhej doon?"}], "end_turn")


def test_guard_blocks_same_turn_confirmation(telegram):
    agent = MunimAgent(llm=_EagerLLM())
    r = agent.ask("Suresh ko reminder bhej do", session_id="t7")
    call = _calls(r, "send_reminder")[0]
    assert call.guard_note and call.output["status"] == "confirmation_required"
    assert telegram == []
