"""Test double for the AI (tests only, never used by the app).

A deterministic keyword-driven stand-in with the same interface as the real LLM services, so the
offline test suite runs without network, without using the free Groq quota, and with stable answers.
Real-model behaviour is checked separately by `python -m evals.run_evals` and MUNIM_TEST_REAL=1.
"""
from __future__ import annotations

import itertools
import re
from datetime import date

from app.agent.tools import _WEEKDAY_ALIASES, fmt_inr
from app.db import store
from app.services.llm_service import LLMResponse, parse_blocks

AFFIRM = re.compile(r"\b(haan|haa|ha|han|hanji|yes|yeah|yep|ok|okay|theek|thik|bhej\s*do|bhejo|kar\s*do|kardo|ho|zaroor|jarur|sure|likh\s*do|likho)\b|हाँ|हां|भेज दो|लिख दो")
AMOUNT = re.compile(r"(?:₹\s*)?(\d[\d,]*)")
RECORD_PAY = re.compile(r"\d[\d,]*\s*(?:rupaye|rupay|rupees|rs\.?|₹)?\s*(?:ka\s*)?(?:diye|de\s*diye|de\s*gaya|de\s*gayi|jama|paid|lautaye|wapas)|रुपये\s*दिए|जमा")
RECORD_CREDIT = re.compile(r"(?:udhaar|udhar)\s*(?:pe|par|mein)?\s*(?:diya|de\s*diya|liya|le\s*gaya|le\s*gayi|likh)|(?:diya|liya|le\s*gaya)\s*(?:udhaar|udhar)|उधार\s*(?:दिया|लिया)")
NEGATE = re.compile(r"\b(nahi|nahin|na|mat|ruko|rukiye|cancel|no|rehne\s*do)\b|नहीं|मत")
REMINDER = re.compile(r"remind|reminder|yaad\s*dila|message\s*(kar|bhej)|msg|रिमाइंडर|याद")
UDHAAR = re.compile(r"udhaar|udhar|udhari|baaki|baki|khata|credit|dena\s*hai|उधार|बाकी")
LAPSED = re.compile(r"band\s*ho|nahi\s*aa|nahin\s*aa|gayab|purane|purana|lapsed|regular|grahak|customer.*(nahi|band)|नहीं आ|बंद हो|पुराने ग्राहक|गायब")
PRODUCT = re.compile(r"\bbik|slow|maal|product|item|category|stock|बिक")
SUMMARY = re.compile(r"hisaab|hisab|bikri|sale|dhanda|dhandha|kaisa|kaisi|kamai|kamaya|business|summary|aaj|today|kal|yesterday|हिसाब|बिक्री|आज|धंधा|कैसा|कल")

CAT_HI = {"groceries": "kirana (atta-dal-chawal)", "snacks": "snacks", "dairy": "doodh-dairy", "household": "gharelu saamaan (sabun, detergent)"}
CHANNEL_HI = {"telegram": "Telegram", "whatsapp": "WhatsApp", "sms": "SMS"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

FALLBACKS = [
    "Ramesh ji, main aapki bikri, udhaar aur customers ka hisaab bata sakta hoon. Jaise poochiye — aaj ka hisaab kaisa raha?",
    "Maaf kijiye, samajh nahi aaya. Aap bikri, udhaar, ya kaunse customer nahi aa rahe — ye pooch sakte hain.",
]


def _ji(name: str) -> str:
    return f"{name.split()[0]} ji"


def _spoken_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]}"


def _text_of(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


def _blocks(msg: dict) -> list:
    c = msg.get("content")
    return c if isinstance(c, list) else []


class FakeLLM:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._fallbacks = itertools.cycle(FALLBACKS)

    def _names(self) -> list[tuple[str, str]]:
        """(alias_lower, canonical) for the current merchant's customers, longest alias first."""
        data = store.load_merchant_data(store.require_merchant())
        names = []
        for c in data["customers"]:
            canon = c["name"]
            for alias in {canon.lower(), canon.split()[0].lower(), *[a.lower() for a in c.get("aliases", [])]}:
                names.append((alias, canon))
        return sorted(names, key=lambda x: -len(x[0]))

    @staticmethod
    def _owner() -> str:
        m = store.get_merchant(store.require_merchant()) if store.current_merchant.get() else None
        return m["owner_name"] if m else "Ramesh"

    # ------------------------------------------------------------ interface
    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        last = messages[-1]
        results = [b for b in _blocks(last) if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            return self._reply_from_tool_result(messages, results[0])
        return self._route(_text_of(last).lower(), messages)

    # ------------------------------------------------------------ helpers
    def _tool(self, name: str, tool_input: dict, preamble: str = "") -> LLMResponse:
        blocks = []
        if preamble:
            blocks.append({"type": "text", "text": preamble})
        blocks.append({"type": "tool_use", "id": f"toolu_mock_{next(self._ids):04d}", "name": name, "input": tool_input})
        return parse_blocks(blocks, "tool_use")

    def _say(self, text: str) -> LLMResponse:
        text = text.replace("Ramesh ji", f"{self._owner()} ji")  # templates are written for the demo store
        return parse_blocks([{"type": "text", "text": text}], "end_turn")

    def _find_name(self, text: str) -> str | None:
        for alias, canon in self._names():
            if re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", text):
                return canon
        return None

    @staticmethod
    def _find_date(text: str) -> str | None:
        m = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if m:
            return m.group(0)
        m = re.search(r"\d{1,2}\s*(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|sept|october|"
                      r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|जनवरी|अगस्त|सितंबर|सितम्बर|अक्टूबर|"
                      r"सप्टेंबर|ऑगस्ट|ऑक्टोबर)|\d{1,2}\s+(?:tarikh|तारीख)|\d{1,2}/\d{1,2}", text, re.I)
        if m:
            return m.group(0)
        tokens = set(re.split(r"[\s,.?!]+", text))
        for names in _WEEKDAY_ALIASES.values():
            for n in names:
                if n in tokens or (len(n) > 4 and n in text):
                    return n
        if tokens & {"kal", "yesterday", "कल"}:
            return "yesterday"
        return None

    @staticmethod
    def _pending_action(messages: list[dict]) -> tuple[str, dict] | None:
        """Most recent confirmable tool call (reminder / udhaar entry), if it was an unconfirmed preview
        made in the previous turn."""
        user_texts = 0
        for msg in reversed(messages[:-1]):
            if msg["role"] == "user" and isinstance(msg["content"], str):
                user_texts += 1
                if user_texts > 1:
                    return None
                continue
            if msg["role"] != "assistant":
                continue
            for b in reversed(_blocks(msg)):
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("send_reminder", "record_udhaar"):
                    return None if b["input"].get("confirmed") else (b["name"], b["input"])
        return None

    # ------------------------------------------------------------ routing
    def _route(self, text: str, messages: list[dict]) -> LLMResponse:
        pending = self._pending_action(messages)
        if pending:
            tool, args = pending
            other_intent = UDHAAR.search(text) or LAPSED.search(text) or PRODUCT.search(text)
            if NEGATE.search(text) and not other_intent:
                what = "reminder nahi bheja" if tool == "send_reminder" else "entry nahi likhi"
                return self._say(f"Theek hai Ramesh ji, {_ji(args['customer_name'])} ka {what}.")
            if AFFIRM.search(text) and not other_intent:
                return self._tool(tool, {**args, "confirmed": True})

        name = self._find_name(text)
        amount = AMOUNT.search(text)
        if not name and amount and RECORD_PAY.search(text):      # unknown/misspelt name: pass it through as spoken
            m = re.match(r"\s*([^\W\d]+)", text)
            name = m.group(1) if m else None
        d = self._find_date(text)
        if d and re.search(r"\d", d) and not (name and amount):    # an explicit date ("15 सितंबर") → that day's summary
            return self._tool("daily_summary", {"date": d})
        if name and amount and (RECORD_PAY.search(text) or RECORD_CREDIT.search(text)):
            kind = "credit" if RECORD_CREDIT.search(text) else "payment"
            return self._tool("record_udhaar", {"customer_name": name, "amount": int(amount.group(1).replace(",", "")),
                                                "kind": kind, "confirmed": False})
        if REMINDER.search(text):
            if not name:
                return self._say("Kisko reminder bhejna hai, Ramesh ji?")
            return self._tool("send_reminder", {"customer_name": name, "channel": "telegram", "confirmed": False})
        if UDHAAR.search(text):
            return self._tool("udhaar_lookup", {"customer_name": name} if name else {})
        if LAPSED.search(text):
            return self._tool("lapsed_customers", {"days_threshold": 10})
        if PRODUCT.search(text):
            return self._tool("product_performance", {"days": 14})
        if SUMMARY.search(text) or self._find_date(text):
            d = self._find_date(text)
            return self._tool("daily_summary", {"date": d} if d else {})
        return self._say(next(self._fallbacks))

    # ------------------------------------------------------------ replies
    def _reply_from_tool_result(self, messages: list[dict], result_block: dict) -> LLMResponse:
        import json

        tool_name = None
        for b in _blocks(messages[-2]):
            if isinstance(b, dict) and b.get("type") == "tool_use" and b["id"] == result_block["tool_use_id"]:
                tool_name = b["name"]
        content = result_block.get("content")
        raw = content if isinstance(content, str) else " ".join(c.get("text", "") for c in content)
        try:
            r = json.loads(raw)
        except ValueError:
            r = {"error": raw}
        if r.get("message") and ("error" in r or r.get("did_you_mean") or r.get("found") is False):
            return self._say(r["message"])     # the tools' own, already-localised explanation
        if "error" in r and tool_name != "send_reminder":
            return self._say("Maaf kijiye Ramesh ji, ye jaankari abhi nahi mil paayi.")
        return self._say(getattr(self, f"_reply_{tool_name}")(r))

    def _reply_daily_summary(self, r: dict) -> str:
        label = "Aaj" if r["is_today"] else f"{r['weekday_hi']}, {_spoken_date(r['date'])} ko"
        out = f"{label} {r['total_sales_display']} ki bikri hui, {r['transaction_count']} payment aaye."
        ins = r["insights"][0] if r["insights"] else None
        if ins and ins["type"] == "weekday_dip":
            out += (f" Dhyan dene wali baat — {ins['weekday_hi']} har hafte sabse dheema din hai, baaki dino se lagbhag "
                    f"{ins['pct_below_other_days']}% kam. {ins['weekday_hi']} ke liye koi chhota offer ya combo rakh ke dekhiye?")
        elif ins and ins["type"] == "lapsed_customers":
            names = [n.split()[0] for n in ins["names"]]
            out += (f" Ek baat — {', '.join(names[:-1])} aur {names[-1]} jaise regular customer kuch hafton se nahi aaye, "
                    f"hafte ka lagbhag {fmt_inr(ins['weekly_spend_at_risk'])} ka dhanda. Ek baar phone kar lijiye?")
        elif ins and ins["type"] == "overdue_udhaar":
            out += (f" Aur {_ji(ins['customer_name'])} ka {fmt_inr(ins['outstanding'])} udhaar "
                    f"{ins['days_since_last_payment']} din se baaki hai — reminder bhej doon?")
        elif ins and ins["type"] == "below_average":
            out += f" Ye aam din se lagbhag {round(ins['pct'])}% kam hai."
        return out

    def _reply_udhaar_lookup(self, r: dict) -> str:
        if not r.get("found"):
            return f"{r['query'].title()} ke naam pe koi udhaar baaki nahi hai."
        if "customer_count" not in r:
            u = r["customers"][0]
            out = f"{_ji(u['customer_name'])} ka {u['outstanding_display']} udhaar baaki hai."
            if not u["last_payment_date"]:
                out += " Abhi tak koi payment nahi aaya."
            elif u["days_since_last_payment"] == 0:
                out += " Aakhri payment aaj hi aaya."
            else:
                out += (f" Aakhri payment {u['days_since_last_payment']} din pehle, "
                        f"{_spoken_date(u['last_payment_date'])} ko aaya tha.")
            if u["is_overdue"]:
                out += " Kahein to unhe reminder bhej doon?"
            return out
        top = r["customers"][0]
        out = f"Kul {r['customer_count']} logon ka {r['total_outstanding_display']} udhaar baaki hai."
        if top["is_overdue"]:
            out += (f" Sabse zaroori {_ji(top['customer_name'])} — {top['outstanding_display']}, "
                    f"{top['days_since_last_payment']} din se payment nahi aaya.")
        return out

    def _reply_lapsed_customers(self, r: dict) -> str:
        if not r["lapsed_count"]:
            return "Sab regular customer aa rahe hain Ramesh ji, koi gayab nahi hai."
        names = [_ji(c["customer_name"]) for c in r["lapsed_customers"]]
        joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} aur {names[-1]}"
        days = [c["days_since_last_visit"] for c in r["lapsed_customers"]]
        return (f"Haan Ramesh ji, {len(names)} regular customer — {joined} — lagbhag {min(days)}-{max(days)} din se nahi aaye. "
                f"Ye hafte ka karib {r['weekly_spend_at_risk_display']} ka dhanda hai. Ek baar phone karke haal-chaal pooch lijiye?")

    def _reply_product_performance(self, r: dict) -> str:
        weak = r["categories_weakest_first"][0]
        slow = r["slowest_products"][0]
        return (f"Pichhle {r['period_days']} din mein {CAT_HI.get(weak['category'], weak['category'])} sabse kam bika, "
                f"kul bikri ka sirf {round(weak['share_pct'])}%. {slow['product']} to lagbhag pada hi hai — sirf "
                f"{slow['units_sold']} bike. Isse counter ke paas ya combo mein rakh ke dekhiye.")

    def _reply_send_reminder(self, r: dict) -> str:
        status = r.get("status")
        if status == "confirmation_required":
            return (f"{_ji(r['customer_name'])} ko {r['outstanding_display']} ke udhaar ka reminder "
                    f"{CHANNEL_HI.get(r['channel'], r['channel'])} pe jayega. Bhej doon?")
        if status == "sent":
            return f"Ho gaya Ramesh ji, {_ji(r['customer_name'])} ko {r['outstanding_display']} ka reminder bhej diya hai."
        if status == "ambiguous":
            return f"Kaunse wale — {' ya '.join(r['candidates'])}?"
        return r.get("message") or "Maaf kijiye, reminder nahi bhej paaya."

    def _reply_record_udhaar(self, r: dict) -> str:
        status = r.get("status")
        who = _ji(r.get("customer_name", ""))
        if status == "confirmation_required":
            if r["kind"] == "payment":
                return f"{who} ka {r['amount_display']} jama likh doon? Uske baad {r['outstanding_after_display']} baaki rahega."
            new = " Ye naya customer hai, khata khul jayega." if r.get("new_customer") else ""
            return f"{who} ke khate mein {r['amount_display']} udhaar likh doon? Total {r['outstanding_after_display']} ho jayega.{new}"
        if status == "saved":
            return f"Likh diya Ramesh ji. {who} ka ab {r['outstanding_after_display']} baaki hai."
        if status == "ambiguous":
            return f"Kaunse wale — {' ya '.join(r['candidates'])}?"
        return r.get("message") or "Maaf kijiye, entry nahi likh paaya."
