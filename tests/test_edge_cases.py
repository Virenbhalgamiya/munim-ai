"""Edge cases: specific days, specific people, odd amounts, odd arguments, and every interface language."""
import re
from datetime import date

import pytest

from app.agent import tools
from app.agent.insights import build_dashboard
from app.i18n import current_lang
from app.parsing import DateParseError, clamp_int, is_valid_name, parse_amount, parse_date

TODAY = date(2026, 10, 2)  # Friday, last day of the demo data


@pytest.fixture
def lang():
    """Run a tool call in a given interface language."""
    tokens = []

    def _set(code):
        tokens.append(current_lang.set(code))
    yield _set
    for tok in reversed(tokens):
        current_lang.reset(tok)


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize("spoken,expected", [
    (None, "2026-10-02"), ("", "2026-10-02"), ("   ", "2026-10-02"), ("aaj", "2026-10-02"), ("आज", "2026-10-02"),
    ("today", "2026-10-02"), ("kal", "2026-10-01"), ("कल", "2026-10-01"), ("काल", "2026-10-01"), ("kal ka", "2026-10-01"),
    ("yesterday", "2026-10-01"), ("parso", "2026-09-30"), ("परसों", "2026-09-30"), ("परवा", "2026-09-30"),
    ("2026-09-15", "2026-09-15"), ("20260915", "2026-09-15"), ("15/9", "2026-09-15"), ("15-09-2026", "2026-09-15"),
    ("15.9.26", "2026-09-15"), ("15 September", "2026-09-15"), ("15th September", "2026-09-15"), ("15 sept", "2026-09-15"),
    ("Sep 15", "2026-09-15"), ("15 सितंबर", "2026-09-15"), ("15 सितम्बर", "2026-09-15"), ("१५ सितंबर", "2026-09-15"),
    ("15 सप्टेंबर", "2026-09-15"), ("2 tarikh", "2026-10-02"), ("1 तारीख", "2026-10-01"), ("28 tarikh", "2026-09-28"),
    ("3 din pehle", "2026-09-29"), ("3 days ago", "2026-09-29"), ("3 दिन पहले", "2026-09-29"), ("3 दिवसांपूर्वी", "2026-09-29"),
    ("Tuesday", "2026-09-29"), ("mangalvaar", "2026-09-29"), ("मंगलवार", "2026-09-29"), ("मंगळवार", "2026-09-29"),
    ("पिछले मंगलवार", "2026-09-29"), ("last tuesday", "2026-09-29"), ("Friday", "2026-10-02"), ("ravivar", "2026-09-27"),
    ("25 December", "2025-12-25"),        # no year + still ahead this year → last year's
])
def test_parse_date(spoken, expected):
    assert parse_date(spoken, TODAY).isoformat() == expected


@pytest.mark.parametrize("bad", ["banana", "2026-02-30", "31/2", "31 February", "kabhi bhi", "next month"])
def test_parse_date_rejects_nonsense(bad):
    with pytest.raises(DateParseError):
        parse_date(bad, TODAY)


def test_specific_date_summary():
    r = tools.daily_summary("15 September")
    assert r["date"] == "2026-09-15" and r["weekday"] == "Tuesday" and r["total_sales"] > 0


@pytest.mark.parametrize("spoken,code", [("2027-01-01", "future_date"), ("2020-01-01", "no_data_for_date"),
                                          ("1 August", "no_data_for_date"), ("banana", "date_not_understood"),
                                          ("2026-02-30", "date_not_understood")])
def test_unusable_dates_give_a_message_not_a_crash(spoken, code):
    r = tools.execute_tool("daily_summary", {"date": spoken})
    assert r["error"] == code and r["message"]


def test_no_data_message_names_the_available_range(lang):
    lang("en")
    r = tools.daily_summary("2020-01-01")
    assert "4 August" in r["message"] and "2 October" in r["message"]


def test_numeric_date_argument_is_accepted():
    assert tools.execute_tool("daily_summary", {"date": 20260915})["date"] == "2026-09-15"


# ------------------------------------------------------------------ people
@pytest.mark.parametrize("spoken", ["Suresh", "suresh", "SURESH", "Suresh ji", "suresh bhaiya ka", "सुरेश", "सुरेश जी"])
def test_name_variants_find_suresh(spoken):
    r = tools.udhaar_lookup(spoken)
    assert r["found"] and [c["customer_name"] for c in r["customers"]] == ["Suresh"]


def test_first_name_outranks_surname():
    r = tools.udhaar_lookup("Suresh Yadav")  # 'Suresh' (first name) beats 'Sunita Yadav' (surname only)
    assert [c["customer_name"] for c in r["customers"]] == ["Suresh"]


@pytest.mark.parametrize("typo", ["Sures", "Sursh", "Suresj"])
def test_typo_gets_did_you_mean_and_nothing_happens(typo, telegram):
    r = tools.udhaar_lookup(typo)
    assert not r["found"] and r["did_you_mean"] == ["Suresh"]
    w = tools.record_udhaar(typo, 100, "payment", confirmed=True)
    assert w["saved"] is False and w["did_you_mean"] == ["Suresh"]
    assert tools.udhaar_lookup("Suresh")["customers"][0]["outstanding"] == 1850
    s = tools.send_reminder(typo, confirmed=True)
    assert s["sent"] is False and telegram == []


def test_typo_never_opens_a_second_khata():
    r = tools.record_udhaar("Sursh", 300, "credit", confirmed=True)
    assert r["saved"] is False and r["did_you_mean"] == ["Suresh"]


def test_unknown_person():
    assert tools.udhaar_lookup("Nobody")["found"] is False
    assert tools.record_udhaar("Nobody", 100, "payment")["status"] == "error"


@pytest.mark.parametrize("bad", ["", "   ", "ji", "bhai ji", "<script>alert(1)</script>", "x" * 61, "12345", "@@@"])
def test_invalid_names_are_refused_for_writes(bad):
    assert tools.record_udhaar(bad, 100, "credit", confirmed=True)["message"]
    assert tools.record_udhaar(bad, 100, "credit", confirmed=True)["saved"] is False
    assert tools.send_reminder(bad, confirmed=True)["sent"] is False
    assert tools.link_upi_payment(bad, confirmed=True)["saved"] is False


def test_honorific_only_lookup_lists_everyone():
    r = tools.udhaar_lookup("ji")
    assert r["found"] and r["customer_count"] >= 5


def test_new_customer_credit_still_works():
    r = tools.record_udhaar("Pooja Kulkarni", 250, "credit", confirmed=True)
    assert r["saved"] and r["new_customer"] and r["outstanding_after"] == 250


def test_huge_name_does_not_crash():
    assert tools.udhaar_lookup("x" * 5000)["found"] is False


# ------------------------------------------------------------------ amounts
@pytest.mark.parametrize("raw,expected", [(500, 500), ("500", 500), ("₹500", 500), ("Rs. 1,000", 1000), ("500 rupees", 500),
                                          ("५००", 500), ("2k", 2000), ("2 hazaar", 2000), (99.6, 100), ("1,00,000", 100000)])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", [float("inf"), float("nan"), "paanch sau", "abc", None, True, "", "5-6"])
def test_parse_amount_rejects(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize("amount", [0, -100, 10**9, float("inf"), float("nan"), "abc"])
def test_bad_amounts_are_refused(amount):
    r = tools.execute_tool("record_udhaar", {"customer_name": "Suresh", "amount": amount, "kind": "payment", "confirmed": True})
    assert r.get("saved") is False and r["message"]


def test_exact_full_repayment_and_one_rupee_over():
    assert tools.record_udhaar("Suresh", 1851, "payment", confirmed=True)["saved"] is False
    r = tools.record_udhaar("Suresh", "₹1,850", "payment", confirmed=True)
    assert r["saved"] and r["outstanding_after"] == 0
    assert tools.record_udhaar("Suresh", 1, "payment", confirmed=True)["saved"] is False


@pytest.mark.parametrize("kind,expected", [("jama", "payment"), ("Paid", "payment"), ("जमा", "payment"),
                                           ("udhaar", "credit"), ("Credit", "credit"), ("उधार", "credit")])
def test_kind_synonyms(kind, expected):
    assert tools.record_udhaar("Suresh", 100, kind)["kind"] == expected


def test_unknown_kind_refused():
    assert tools.record_udhaar("Suresh", 100, "refund")["status"] == "error"


# ------------------------------------------------------------------ other arguments
@pytest.mark.parametrize("v,expected", [(0, 1), (-5, 1), (10000, 60), ("abc", 10), ("7", 7), (None, 10), (7.9, 7)])
def test_lapsed_threshold_is_clamped(v, expected):
    assert tools.execute_tool("lapsed_customers", {"days_threshold": v})["days_threshold"] == expected


@pytest.mark.parametrize("v,expected", [(0, 1), (-3, 1), (9999, 30), ("7", 7)])
def test_product_days_are_clamped(v, expected):
    assert tools.execute_tool("product_performance", {"days": v})["period_days"] == expected


def test_bad_tool_calls_return_errors():
    assert "error" in tools.execute_tool("nope", {})
    assert "error" in tools.execute_tool("daily_summary", {"bogus": 1})
    assert "error" in tools.execute_tool("record_udhaar", {"customer_name": "Suresh"})


def test_clamp_and_name_helpers():
    assert clamp_int("12", 5, 1, 10) == 10 and clamp_int(None, 5, 1, 10) == 5
    assert is_valid_name("Meena tai") and is_valid_name("सुरेश") and not is_valid_name("ji")


# ------------------------------------------------------------------ languages
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _dashboard_text(d):
    parts = [d["briefing"], d["greeting"]["date_display"], d["greeting"]["owner"]]
    for k in d["kpis"]:
        parts += [k["label"], k.get("sub") or "", k.get("delta_label") or ""]
    for i in d["insights"]:
        parts += [i["title"], i["detail"], i["impact_label"], (i.get("action") or {}).get("label", "")]
    parts += [c["label"] for c in d["categories"]] + [s["label"] for s in d["sales_28d"]] + [w["short"] for w in d["weekday_avg"]]
    return " ".join(parts)


def test_english_dashboard_has_no_devanagari():
    d = build_dashboard("sharma-gs", "en")
    assert d["lang"] == "en"
    text = _dashboard_text(d)
    assert not DEVANAGARI.search(text), DEVANAGARI.findall(text)[:20]
    assert d["greeting"]["owner"] == "Ramesh" and "Tuesday" in text


@pytest.mark.parametrize("code,word", [("hi", "मंगलवार"), ("mr", "मंगळवार")])
def test_hindi_and_marathi_dashboards(code, word):
    d = build_dashboard("sharma-gs", code)
    assert d["lang"] == code and DEVANAGARI.search(d["briefing"])
    assert d["weakest_weekday"] == "Tuesday" and d["weakest_weekday_hi"] == word


def test_dashboards_differ_per_language_and_unknown_falls_back():
    hi, en, mr = (build_dashboard("sharma-gs", c)["briefing"] for c in ("hi", "en", "mr"))
    assert len({hi, en, mr}) == 3
    assert build_dashboard("sharma-gs", "xx")["lang"] == "hi"


@pytest.mark.parametrize("code,needle", [("en", "Did you mean"), ("hi", "क्या आपका मतलब"), ("mr", "म्हणायचे")])
def test_tool_messages_follow_language(lang, code, needle):
    lang(code)
    assert needle in tools.udhaar_lookup("Sures")["message"]
    assert tools.daily_summary("banana")["message"]


def test_english_messages_use_latin_names(lang):
    lang("en")
    r = tools.record_udhaar("Suresh", 5000, "payment")
    assert "Suresh" in r["message"] and not DEVANAGARI.search(r["message"].replace("₹", ""))


def test_dashboard_endpoint_language(client=None):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        from tests.test_production import login
        login(c)
        en = c.get("/api/dashboard?lang=en").json()
        mr = c.get("/api/dashboard?lang=mr").json()
        bad = c.get("/api/dashboard?lang=zz")
    assert en["lang"] == "en" and mr["lang"] == "mr"
    assert bad.status_code in (200, 422)


# ------------------------------------------------------------------ reply language + voice
from app.services.tts_service import speech_lang  # noqa: E402


@pytest.mark.parametrize("text,lang,expected", [
    ("हो गया, सुरेश जी को रिमाइंडर भेज दिया गया।", "en", "hi"),   # Hindi text on the English screen → Hindi voice
    ("Done, the reminder was sent to Suresh ji.", "en", "en"),
    ("आज ₹13,535 की बिक्री हुई", "hi", "hi"),
    ("नमस्कार रमेश, आज विक्री झाली", "mr", "mr"),
    ("Paytm UPI", "hi", "en"),
])
def test_voice_follows_the_script_of_the_text(text, lang, expected):
    assert speech_lang(text, lang) == expected


class _HindiThenEnglish:
    """Stub AI that answers in Hindi first and in English only when nudged."""
    def __init__(self):
        self.calls = []

    def chat(self, system, messages, tools):
        from app.services.llm_service import parse_blocks
        self.calls.append(system)
        text = "Done, the reminder was sent to Suresh ji." if "was not in English" in system else "हो गया, सुरेश जी को रिमाइंडर भेज दिया गया।"
        return parse_blocks([{"type": "text", "text": text}], "end_turn")


def test_english_screen_gets_an_english_reply_even_if_the_ai_slips():
    from app.agent.agent import MunimAgent
    stub = _HindiThenEnglish()
    r = MunimAgent(llm=stub).ask("thanks", session_id="lang-1", merchant_id="sharma-gs", lang="en")
    assert r.reply == "Done, the reminder was sent to Suresh ji." and len(stub.calls) == 2
    assert "English" in stub.calls[0]


def test_hindi_screen_keeps_hindi_reply():
    from app.agent.agent import MunimAgent
    stub = _HindiThenEnglish()
    r = MunimAgent(llm=stub).ask("धन्यवाद", session_id="lang-2", merchant_id="sharma-gs", lang="hi")
    assert r.reply.startswith("हो गया") and len(stub.calls) == 1


# ------------------------------------------------------------------ "yes" must confirm, deterministically
from app.agent.agent import is_clear_yes  # noqa: E402


@pytest.mark.parametrize("text", ["yes", "Yes, send it", "ok", "okay go ahead", "haan bhej do", "हाँ, भेज दो", "हो, पाठवा", "theek hai"])
def test_clear_yes(text):
    assert is_clear_yes(text)


@pytest.mark.parametrize("text", ["no", "nahi", "yes but not now", "haan, aur Meena ka udhaar kitna hai?",
                                  "send it to Meena instead", "what?", "", "मत भेजो"])
def test_not_a_clear_yes(text):
    assert not is_clear_yes(text)


class _AlwaysPreviews:
    """Stub AI that never sets confirmed=true (what a weaker fallback model sometimes does on a bare 'yes')."""
    def chat(self, system, messages, tools):
        from app.services.llm_service import parse_blocks
        last = messages[-1]["content"]
        if isinstance(last, list) and last and last[0].get("type") == "tool_result":
            return parse_blocks([{"type": "text", "text": "Reminder for Suresh ji. Shall I send it?"}], "end_turn")
        return parse_blocks([{"type": "tool_use", "id": f"t{len(messages)}", "name": "send_reminder",
                              "input": {"customer_name": "Suresh", "confirmed": False}}], "tool_use")


def test_yes_to_the_previous_preview_sends_once(telegram):
    from app.agent.agent import MunimAgent
    agent = MunimAgent(llm=_AlwaysPreviews())
    agent.ask("Send Suresh a payment reminder", session_id="yes-1", merchant_id="sharma-gs", lang="en")
    assert telegram == []
    r = agent.ask("yes", session_id="yes-1", merchant_id="sharma-gs", lang="en")
    assert r.tool_calls[-1].output["status"] == "sent" and len(telegram) == 1


def test_yes_without_a_preview_last_turn_sends_nothing(telegram):
    from app.agent.agent import MunimAgent
    agent = MunimAgent(llm=_AlwaysPreviews())
    r = agent.ask("yes", session_id="yes-2", merchant_id="sharma-gs", lang="en")   # no preview before
    assert r.tool_calls[-1].output["status"] == "confirmation_required" and telegram == []


def test_no_after_preview_sends_nothing(telegram):
    from app.agent.agent import MunimAgent
    agent = MunimAgent(llm=_AlwaysPreviews())
    agent.ask("Send Suresh a payment reminder", session_id="yes-3", merchant_id="sharma-gs", lang="en")
    agent.ask("no", session_id="yes-3", merchant_id="sharma-gs", lang="en")
    assert telegram == []


class _SendsThenFails:
    """Stub AI: previews, then on 'yes' confirms, but the call that should word the reply fails (rate limit)."""
    def __init__(self):
        self.n = 0

    def chat(self, system, messages, tools):
        from app.services.llm_service import parse_blocks
        last = messages[-1]["content"]
        if isinstance(last, list) and last and last[0].get("type") == "tool_result":
            if '"sent"' in last[0]["content"]:
                raise RuntimeError("groq API error 429: rate limit")
            return parse_blocks([{"type": "text", "text": "Shall I send it?"}], "end_turn")
        confirmed = messages[-1]["content"] == "yes"
        return parse_blocks([{"type": "tool_use", "id": f"t{len(messages)}", "name": "send_reminder",
                              "input": {"customer_name": "Suresh", "confirmed": confirmed}}], "tool_use")


@pytest.mark.parametrize("code,needle", [("en", "Done, the reminder was sent to Suresh"), ("hi", "सुरेश को Telegram पर रिमाइंडर भेज दिया"),
                                          ("mr", "सुरेश यांना Telegram")])
def test_action_done_is_reported_even_if_ai_fails_afterwards(telegram, code, needle):
    from app.agent.agent import MunimAgent
    agent = MunimAgent(llm=_SendsThenFails())
    agent.ask("Send Suresh a payment reminder", session_id=f"f-{code}", merchant_id="sharma-gs", lang=code)
    r = agent.ask("yes", session_id=f"f-{code}", merchant_id="sharma-gs", lang=code)
    assert len(telegram) == 1 and needle in r.reply


# ------------------------------------------------------------------ past chats are kept
def test_past_conversations_are_listed_newest_first():
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.test_production import login
    with TestClient(app) as c:
        login(c)
        c.post("/api/ask", json={"text": "आज का हिसाब कैसा रहा?", "session_id": "chat-old", "lang": "hi"})
        c.post("/api/ask", json={"text": "किसका कितना उधार बाकी है?", "session_id": "chat-new", "lang": "hi"})
        items = c.get("/api/conversations").json()["conversations"]
        old = c.get("/api/conversation?session_id=chat-old").json()["messages"]
    assert [i["session_id"] for i in items[:2]] == ["chat-new", "chat-old"]
    assert items[1]["title"] == "आज का हिसाब कैसा रहा?" and items[1]["questions"] == 1
    assert old[0]["text"] == "आज का हिसाब कैसा रहा?" and len(old) == 2


# ------------------------------------------------------------------ AI outages: second provider + plain facts
class _Down:
    provider, model, last_model_used = "groq", "m", None

    def chat(self, *a):
        raise RuntimeError("groq API error 429: rate limit")


class _Says:
    provider, model, last_model_used = "gemini", "g", None

    def chat(self, system, messages, tools):
        from app.services.llm_service import parse_blocks
        return parse_blocks([{"type": "text", "text": "ok from backup"}], "end_turn")


def test_chain_uses_second_provider_when_first_is_limited():
    from app.services.llm_service import ChainLLMService
    chain = ChainLLMService([_Down(), _Says()])
    assert chain.chat("s", [{"role": "user", "content": "hi"}], []).text == "ok from backup"
    assert chain.last_model_used.startswith("gemini")


def test_chain_raises_when_every_provider_fails():
    from app.services.llm_service import ChainLLMService
    with pytest.raises(RuntimeError):
        ChainLLMService([_Down(), _Down()]).chat("s", [{"role": "user", "content": "hi"}], [])


class _ToolThenFails:
    def __init__(self, tool, args=None):
        self.tool, self.args = tool, args or {}

    def chat(self, system, messages, tools):
        from app.services.llm_service import parse_blocks
        last = messages[-1]["content"]
        if isinstance(last, list) and last and last[0].get("type") == "tool_result":
            raise RuntimeError("all AI providers failed")
        return parse_blocks([{"type": "tool_use", "id": "t1", "name": self.tool, "input": self.args}], "tool_use")


@pytest.mark.parametrize("tool,args,lang,needle", [
    ("lapsed_customers", {}, "hi", "आना बंद हो गए"), ("lapsed_customers", {}, "en", "have stopped coming"),
    ("daily_summary", {}, "hi", "₹13,535"), ("daily_summary", {"date": "Tuesday"}, "en", "Tuesday: sales of ₹7,437"),
    ("udhaar_lookup", {}, "en", "Total udhaar"), ("udhaar_lookup", {"customer_name": "Suresh"}, "mr", "सुरेश यांची ₹1,850"),
    ("product_performance", {}, "en", "Household sells the least"),
])
def test_facts_are_stated_when_ai_fails_after_fetching_data(tool, args, lang, needle):
    from app.agent.agent import MunimAgent
    r = MunimAgent(llm=_ToolThenFails(tool, args)).ask("q", session_id=f"facts-{tool}-{lang}", merchant_id="sharma-gs", lang=lang)
    assert needle in r.reply and "AI" not in r.reply


# ------------------------------------------------------------------ an earlier outage must not poison the chat
def test_failed_turns_are_hidden_from_the_ai():
    from app.agent.agent import drop_failed_turns
    hist = [
        {"role": "user", "content": "aaj ka hisaab?"},
        {"role": "assistant", "content": [{"type": "text", "text": "आज ₹13,535 की बिक्री हुई।"}]},
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": [{"type": "text", "text": "Sorry, I couldn't reach the AI right now. Please check the internet."}]},
        {"role": "user", "content": "udhaar?"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "udhaar_lookup", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "{}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "माफ़ कीजिए, अभी AI से जुड़ नहीं पाया।"}]},
    ]
    out = drop_failed_turns(hist)
    assert [m["content"] if isinstance(m["content"], str) else m["content"][0].get("text") for m in out] == \
        ["aaj ka hisaab?", "आज ₹13,535 की बिक्री हुई।"]


class _CopiesHistory:
    """Stub AI that parrots the last assistant text it sees (what a model does with a poisoned history)."""
    def chat(self, system, messages, tools):
        from app.services.llm_service import parse_blocks
        prev = [m for m in messages if m["role"] == "assistant"]
        text = prev[-1]["content"][0]["text"] if prev else "fresh answer"
        return parse_blocks([{"type": "text", "text": text}], "end_turn")


def test_ai_does_not_repeat_an_old_apology():
    from app.agent.agent import MunimAgent
    from app.db import store
    store.save_turn("sharma-gs", "poisoned", 1, {}, [
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": [{"type": "text", "text": "Sorry, I couldn't reach the AI right now."}]}])
    r = MunimAgent(llm=_CopiesHistory()).ask("hello", session_id="poisoned", merchant_id="sharma-gs", lang="en")
    assert r.reply == "fresh answer"
