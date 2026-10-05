"""Munim AI agent loop.

text in -> LLM (with tools) -> execute tool calls -> feed results back -> ... -> spoken reply out.

Production behaviour:
- Every turn runs inside the merchant's scope; tools only ever see that merchant's data.
- Conversation + confirmation state are persisted in SQLite, so a restart doesn't lose context.
- Two-turn confirmation guard for every tool that changes data or contacts a customer
  (CONFIRM_TOOLS): confirmed=true only goes through if the merchant was shown a preview of the
  same action in the immediately previous turn. Otherwise it is downgraded to a preview.
- If the AI provider fails (network, rate limit on every model, outage), the merchant gets a short
  honest apology, and the saved history stays valid for the next question.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app import config
from app.agent.system_prompt import TOOLS, build_system_prompt
from app.agent.tools import CONFIRM_TOOLS, _data, execute_tool, get_today, match_customer
from app.db import store
from app.observability import metrics
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5

# Common Marathi words that don't occur in Hindi (Hindi: है / कितना / हुई / क्या ...)
_MARATHI_ONLY = ("आहे", "किती", "झाली", "झाला", "काय", "कसा", "कशी", "पाठव", "विक्री", "माझ")


_MARATHI_ROMAN = {"aahe", "ahe", "kiti", "jhali", "zali", "jhala", "zala", "pathav", "pathva", "vikri", "majha", "majhi", "kashi"}


def detect_reply_language(text: str, lang: str = "hi") -> str:
    """Language/script for this reply. Hindi is always written in Devanagari (also for Hinglish input).
    Free models drift unless told on every turn."""
    low = (text or "").lower()
    if lang == "mr" or any(m in text for m in _MARATHI_ONLY) or set(re.findall(r"[a-z]+", low)) & _MARATHI_ROMAN:
        return "Marathi in Devanagari script"
    if lang == "en":
        return ("English (he chose English in the app). Write the WHOLE reply in English, Latin letters only, "
                "names like 'Suresh ji'. Do not write Hindi or Devanagari")
    if any("\u0900" <= ch <= "\u097f" for ch in text):
        return "Hindi in Devanagari script"
    return "Hindi in Devanagari script (he wrote Hinglish in Roman letters; understand it, but reply in Devanagari)"


# Fresh-data guard: a reply that quotes figures must come from a tool call made in the same turn.
_NUMBER = re.compile(r"₹\s?\d|\d[\d,]{2,}|\b\d+\s?(%|din|days?|दिन|payment|पेमेंट|transactions?)", re.I)
LANG_NUDGE = ("\nIMPORTANT: Your draft was not in English. The merchant chose English in the app. Write the same "
              "answer again in simple English using Latin letters only (names like 'Suresh ji', amounts as ₹ digits).\n")


def _mostly_devanagari(text: str) -> bool:
    letters = [ch for ch in text or "" if ch.isalpha()]
    return bool(letters) and sum("\u0900" <= ch <= "\u097f" for ch in letters) > len(letters) * 0.3


# A short, unambiguous "yes" to the preview shown in the previous turn (en / hi / Hinglish / mr).
_YES_CORE = {"yes", "yeah", "yep", "ok", "okay", "sure", "confirm", "send", "haan", "han", "haa", "hanji", "haanji",
             "theek", "thik", "ho", "hoy", "bhej", "bhejo", "likh", "likho", "kar", "jama", "हाँ", "हां", "ठीक", "हो",
             "होय", "भेज", "भेजो", "लिख", "लिखो", "कर", "जमा", "बरोबर", "पाठवा", "पाठव", "हो,"}
_YES_FILLER = {"ji", "jee", "hai", "do", "de", "dijiye", "please", "it", "go", "ahead", "now", "na", "bhai", "yaar", "chalo",
               "जी", "है", "दो", "दे", "दीजिए", "करो", "दिया", "हाँजी", "ना", "चालेल", "करा", "lo", "kardo", "bhejdo", "likhdo"}
_NO_WORDS = {"no", "not", "dont", "don't", "nahi", "nahin", "mat", "ruko", "cancel", "stop", "wait", "नहीं", "मत", "रुको",
             "नको", "नाही", "थांबा"}


_APOLOGY_MARKERS = ("couldn't reach the ai", "couldn't answer that just now", "ai से जुड़ नहीं", "जवाब नहीं दे पाया",
                    "ai शी जोडता आले नाही", "उत्तर देता आले नाही")


def _is_apology(msg: dict) -> bool:
    if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
        return False
    text = " ".join(b.get("text", "") for b in msg["content"] if isinstance(b, dict) and b.get("type") == "text").lower()
    return bool(text) and any(m in text for m in _APOLOGY_MARKERS)


def drop_failed_turns(history: list[dict]) -> list[dict]:
    """Remove turns that ended in an apology (AI was down) from what the model sees. Otherwise the model
    reads its own "couldn't reach the AI" and copies it, even when it is working again. They stay on screen."""
    out: list[dict] = []
    for msg in history:
        if _is_apology(msg):
            while out and not (out[-1]["role"] == "user" and isinstance(out[-1]["content"], str)):
                out.pop()                                   # tool calls/results of the failed turn
            if out:
                out.pop()                                   # the question itself
            continue
        out.append(msg)
    return out


def _done_message(traces, lang: str) -> str | None:
    """If an action already succeeded this turn, say so plainly even when the AI fails to word the reply,
    so the merchant never hears "couldn't reach the AI" for a reminder that was actually sent."""
    from app.i18n import name_for, t
    lang = lang if lang in ("hi", "en", "mr") else "hi"
    for tr in reversed(traces):
        o = tr.output or {}
        if o.get("status") not in ("sent", "saved"):
            continue
        name = name_for(o, lang)
        if tr.name == "send_reminder":
            return t("done.reminder", lang, name=name)
        if tr.name == "record_udhaar":
            return t("done.udhaar", lang, name=name, amt=o.get("amount_display", ""), after=o.get("outstanding_after_display", ""))
        if tr.name == "link_upi_payment":
            return t("done.upi", lang, name=name)
    return _facts_message(traces, lang)


def _facts_message(traces, lang: str) -> str | None:
    """The data was fetched but the AI couldn't word the answer: state the key facts plainly.
    (The rich card with the full numbers is shown next to it.)"""
    from app.i18n import CATEGORY, WEEKDAY, join_names, name_for, t
    for tr in reversed(traces):
        o = tr.output or {}
        if o.get("error") or o.get("status") == "error":
            continue
        if tr.name == "daily_summary" and o.get("total_sales_display"):
            day = t("fb.today", lang) if o.get("is_today") else WEEKDAY.get(lang, WEEKDAY["en"]).get(o.get("weekday"), o.get("weekday"))
            return t("fb.summary", lang, day=day, amt=o["total_sales_display"], n=o.get("transaction_count", 0))
        if tr.name == "udhaar_lookup" and o.get("found"):
            cs = o.get("customers") or []
            if "customer_count" not in o and cs:
                return t("fb.udhaar_one", lang, name=name_for(cs[0], lang), amt=cs[0]["outstanding_display"])
            top = ", ".join(f"{name_for(c, lang)} {c['outstanding_display']}" for c in sorted(cs, key=lambda c: -c["outstanding"])[:3])
            return t("fb.udhaar_all", lang, total=o.get("total_outstanding_display", ""), n=o.get("customer_count", len(cs)), top=top)
        if tr.name == "lapsed_customers":
            cs = o.get("lapsed_customers") or []
            if not cs:
                return t("fb.lapsed_none", lang)
            return t("fb.lapsed", lang, names=join_names([name_for(c, lang) for c in cs], lang), amt=o.get("weekly_spend_at_risk_display", ""))
        if tr.name == "product_performance" and o.get("categories_weakest_first"):
            c = o["categories_weakest_first"][0]
            cat = CATEGORY.get(lang, CATEGORY["en"]).get(c["category"], c["category"])
            items = ", ".join(p["product"] for p in (o.get("slowest_products") or [])[:3])
            return t("fb.product", lang, cat=cat, p=round(c["share_pct"]), items=items)
        if tr.name == "pending_upi_payments" and o.get("count"):
            return t("fb.upi", lang, n=o["count"])
    return None


def is_clear_yes(text: str) -> bool:
    toks = [t for t in re.split(r"[\s,.!?।]+", (text or "").lower()) if t]
    if not toks or len(toks) > 6 or any(t in _NO_WORDS for t in toks):
        return False
    return any(t in _YES_CORE for t in toks) and all(t in _YES_CORE or t in _YES_FILLER for t in toks)


STALE_NUDGE = ("\nIMPORTANT: Your draft quoted numbers without calling a tool in this turn. Numbers from earlier "
               "turns may be outdated. Call the right tool now and answer only from its result.\n")
APOLOGY_AI_DOWN = "माफ़ कीजिए, अभी AI से जुड़ नहीं पाया। इंटरनेट देखकर थोड़ी देर बाद फिर पूछिए।"
APOLOGY_NO_ANSWER = "माफ़ कीजिए, अभी जवाब नहीं दे पाया। एक बार फिर पूछिए?"
APOLOGIES = {
    "hi": (APOLOGY_AI_DOWN, APOLOGY_NO_ANSWER),
    "en": ("Sorry, I couldn't reach the AI right now. Please check the internet and ask again in a moment.",
           "Sorry, I couldn't answer that just now. Could you ask again?"),
    "mr": ("माफ करा, आत्ता AI शी जोडता आले नाही. इंटरनेट तपासून थोड्या वेळाने पुन्हा विचारा.",
           "माफ करा, आत्ता उत्तर देता आले नाही. पुन्हा एकदा विचाराल का?"),
}


def _quotes_numbers(text: str) -> bool:
    return bool(text and _NUMBER.search(text))


def _canonical_customer(name: str, records: list[dict], key: str) -> str:
    """'Suresh ji' / 'suresh bhai' / 'सुरेश' -> 'suresh' (so preview and confirm match)."""
    matches = match_customer(name, records, key=key)
    return (matches[0][key] if len(matches) == 1 else name).strip().lower()


def action_key(tool: str, tool_input: dict) -> str:
    """Identity of a confirmable action. Confirm must match the previewed action exactly."""
    data = _data()
    if tool == "send_reminder":
        return f"send_reminder:{_canonical_customer(str(tool_input.get('customer_name', '')), data['udhaar'], 'customer_name')}"
    if tool == "record_udhaar":
        who = _canonical_customer(str(tool_input.get("customer_name", "")), data["customers"], "name")
        try:
            amount = int(round(float(tool_input.get("amount", 0))))
        except (TypeError, ValueError):
            amount = 0
        return f"record_udhaar:{who}:{str(tool_input.get('kind', '')).lower()}:{amount}"
    if tool == "link_upi_payment":
        who = _canonical_customer(str(tool_input.get("customer_name", "")), data["customers"], "name")
        return f"link_upi_payment:{who}:{bool(tool_input.get('as_purchase'))}"
    return tool


@dataclass
class ToolTrace:
    name: str
    input: dict
    output: dict
    guard_note: str | None = None


@dataclass
class AgentResult:
    session_id: str
    user_text: str
    reply: str
    tool_calls: list[ToolTrace] = field(default_factory=list)
    error: bool = False      # the AI provider could not be reached for this turn

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_text": self.user_text,
            "reply": self.reply,
            "error": self.error,
            "tool_calls": [t.__dict__ for t in self.tool_calls],
        }


@dataclass
class Session:
    turn: int = 0
    pending: dict[str, int] = field(default_factory=dict)   # action_key -> turn the preview was shown
    messages: list[dict] = field(default_factory=list)


class MunimAgent:
    def __init__(self, llm=None) -> None:
        self.llm = llm or get_llm_service()

    # ------------------------------------------------------------ helpers
    def _system_prompt(self, merchant_id: str, text: str, lang: str = "hi") -> str:
        m = store.get_merchant(merchant_id)
        today = get_today()
        today_line = (f"Today is {today:%A, %d %B %Y}. For today, omit `date`; for other days pass the spoken word, "
                      f"not a computed date.\nReply language for this turn: {detect_reply_language(text, lang)}. "
                      f"Amounts stay as ₹ digits.")
        return build_system_prompt(m.get("owner_name_hi") or m["owner_name"], m.get("store_name_hi") or m["store_name"],
                                   m["location"], today_line)

    def reset(self, session_id: str, merchant_id: str | None = None) -> None:
        store.reset_session(merchant_id or config.DEFAULT_MERCHANT_ID, session_id)

    def _guarded_execute(self, session: Session, name: str, tool_input: dict,
                         affirmed: bool = False) -> tuple[dict, str | None]:
        if name not in CONFIRM_TOOLS:
            return execute_tool(name, tool_input), None
        tool_input = dict(tool_input)
        key = action_key(name, tool_input)
        note = None
        if (not tool_input.get("confirmed") and affirmed and session.pending.get(key) == session.turn - 1):
            # He clearly said "yes" to exactly this preview last turn, but the model asked again:
            # treat it as the confirmation instead of looping on "shall I send it?".
            tool_input["confirmed"] = True
            note = "guard: clear 'yes' to the previous turn's preview of this exact action; confirmed"
            metrics.inc("guard_upgraded")
        elif tool_input.get("confirmed"):
            shown_in = session.pending.get(key)
            if shown_in is None or shown_in != session.turn - 1:
                tool_input["confirmed"] = False
                note = ("guard: confirmed=true blocked (merchant did not approve a preview of this exact action in the "
                        "previous turn); downgraded to preview")
                metrics.inc("guard_blocked")
                logger.warning("confirmation guard blocked %s", name, extra={"action": key})
        output = execute_tool(name, tool_input)
        status = output.get("status")
        if status == "confirmation_required":
            session.pending[key] = session.turn
        elif status in ("sent", "saved"):
            session.pending.pop(key, None)
        if note:
            output = {**output, "guard_note": note}
        return output, note

    def _chat(self, system: str, messages: list[dict]):
        t0 = time.perf_counter()
        try:
            resp = self.llm.chat(system, messages, TOOLS)
        except Exception as e:
            metrics.inc("llm_failures")
            logger.warning("LLM call failed: %s", e, extra={"provider": config.LLM_PROVIDER})
            raise
        metrics.inc("llm_calls")
        metrics.observe("llm_call", time.perf_counter() - t0)
        return resp

    # ------------------------------------------------------------ main entry
    def ask(self, text: str, session_id: str = "default", merchant_id: str | None = None, on_event=None,
            lang: str = "hi") -> AgentResult:
        """Run one merchant turn. on_event(kind, payload) is an optional callback for CLI tracing."""
        from app.i18n import current_lang, lang_or_default
        merchant_id = merchant_id or store.current_merchant.get() or config.DEFAULT_MERCHANT_ID
        token = current_lang.set(lang_or_default(lang))
        try:
            with store.merchant_scope(merchant_id):
                return self._ask(text, session_id, merchant_id, on_event, lang)
        finally:
            current_lang.reset(token)

    def _ask(self, text: str, session_id: str, merchant_id: str, on_event, lang: str = "hi") -> AgentResult:
        t_start = time.perf_counter()
        turn, pending, history = store.load_session(merchant_id, session_id)
        history = drop_failed_turns(history)
        session = Session(turn=turn + 1, pending=pending, messages=history)
        new_messages: list[dict] = [{"role": "user", "content": text}]
        session.messages.append(new_messages[0])
        traces: list[ToolTrace] = []
        emit = on_event or (lambda *_: None)
        system = self._system_prompt(merchant_id, text, lang if lang in ("hi", "mr", "en") else "hi")
        affirmed = is_clear_yes(text)
        failed = False

        reply = ""
        used_tool, nudged = False, False
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                resp = self._chat(system, session.messages)
                if not resp.tool_calls and not used_tool and not nudged and _quotes_numbers(resp.text):
                    # The AI answered with figures without fetching data this turn (e.g. copied from an
                    # earlier answer, which may be stale). Discard the draft and make it use a tool.
                    nudged = True
                    metrics.inc("stale_answer_retries")
                    logger.info("numeric answer without a tool call; retrying with fresh-data instruction")
                    resp = self._chat(system + STALE_NUDGE, session.messages)
                used_tool = used_tool or bool(resp.tool_calls)
                content = resp.content or [{"type": "text", "text": resp.text or "..."}]
                msg = {"role": "assistant", "content": content}
                session.messages.append(msg)
                new_messages.append(msg)
                if resp.text:
                    emit("llm_text", resp.text)
                if not resp.tool_calls:
                    reply = resp.text
                    break

                results: list[dict[str, Any]] = []
                for call in resp.tool_calls:
                    emit("tool_call", {"name": call.name, "input": call.input})
                    output, note = self._guarded_execute(session, call.name, call.input, affirmed)
                    metrics.inc(f"tool.{call.name}")
                    if note:
                        emit("guard", note)
                    emit("tool_result", {"name": call.name, "output": output})
                    traces.append(ToolTrace(call.name, call.input, output, note))
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(output, ensure_ascii=False, default=str, separators=(",", ":")),
                        **({"is_error": True} if "error" in output else {}),
                    })
                # All tool results for one assistant message go back in a single user message.
                msg = {"role": "user", "content": results}
                session.messages.append(msg)
                new_messages.append(msg)
            else:
                reply = reply or APOLOGIES.get(lang, APOLOGIES["hi"])[1]
            if lang == "en" and reply and _mostly_devanagari(reply):
                # The model slipped back into Hindi: ask once for the same answer in English (the screen
                # and the voice are English, and an English voice cannot read Devanagari).
                metrics.inc("language_retries")
                retry = self._chat(system + LANG_NUDGE, session.messages[:-1])
                if retry.text and not retry.tool_calls and not _mostly_devanagari(retry.text):
                    reply = retry.text
                    session.messages[-1]["content"] = [{"type": "text", "text": reply}]
        except Exception:
            logger.exception("agent turn failed")
            metrics.inc("agent_errors")
            failed = True
            reply = _done_message(traces, lang) or APOLOGIES.get(lang, APOLOGIES["hi"])[0]
            # Persist only question + apology, so history never ends on an unanswered tool call.
            new_messages = [new_messages[0], {"role": "assistant", "content": [{"type": "text", "text": reply}]}]

        # Drop previews older than the previous turn: confirmation must come in the very next turn.
        session.pending = {k: v for k, v in session.pending.items() if v >= session.turn - 1}
        store.save_turn(merchant_id, session_id, session.turn, session.pending, new_messages)
        metrics.observe("agent_turn", time.perf_counter() - t_start)
        logger.info("turn done", extra={"session_id": session_id, "tools": [t.name for t in traces],
                                        "failed": failed, "ms": round((time.perf_counter() - t_start) * 1000)})
        return AgentResult(session_id=session_id, user_text=text, reply=reply, tool_calls=traces, error=failed)
