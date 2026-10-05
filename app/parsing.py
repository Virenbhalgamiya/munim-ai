"""Forgiving parsers for what a shopkeeper actually says (or what the LLM passes through as spoken).

Dates:   "aaj", "kal", "parso", "काल" (mr), "परवा" (mr), weekday names in en/hi/mr/Hinglish, "15 September",
         "Sep 15", "15 सितंबर", "15/9", "15-09-2026", "2026-09-15", "2 tarikh" / "2 तारीख", "3 din pehle",
         "3 days ago", "3 दिन पहले", "3 दिवसांपूर्वी".
Amounts: 500, "500", "₹500", "Rs. 1,000", "500 rupees", "५००" (Devanagari digits).
Names:   honorifics stripped; first-name matches outrank surname matches; close typos are offered as
         suggestions (never acted on silently).
"""
from __future__ import annotations

import difflib
import math
import re
from datetime import date, timedelta

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_MONTHS = {
    1: ["january", "jan", "जनवरी", "जानेवारी"],
    2: ["february", "feb", "फ़रवरी", "फरवरी", "फेब्रुवारी"],
    3: ["march", "mar", "मार्च"],
    4: ["april", "apr", "अप्रैल", "एप्रिल"],
    5: ["may", "मई", "मे"],
    6: ["june", "jun", "जून"],
    7: ["july", "jul", "जुलाई", "जुलै"],
    8: ["august", "aug", "अगस्त", "ऑगस्ट", "ओगस्ट"],
    9: ["september", "sept", "sep", "सितंबर", "सितम्बर", "सप्टेंबर"],
    10: ["october", "oct", "अक्टूबर", "अक्तूबर", "ऑक्टोबर"],
    11: ["november", "nov", "नवंबर", "नवम्बर", "नोव्हेंबर"],
    12: ["december", "dec", "दिसंबर", "दिसम्बर", "डिसेंबर"],
}
_MONTH_LOOKUP = {name: m for m, names in _MONTHS.items() for name in names}

WEEKDAY_ALIASES = {
    0: ["monday", "mon", "somvar", "somwar", "somvaar", "सोमवार"],
    1: ["tuesday", "tue", "tues", "mangalvar", "mangalwar", "mangalvaar", "mangal", "मंगलवार", "मंगल", "मंगळवार", "मंगळ"],
    2: ["wednesday", "wed", "budhvar", "budhwar", "budhvaar", "बुधवार"],
    3: ["thursday", "thu", "thurs", "guruvar", "guruwar", "guruvaar", "brihaspativar", "गुरुवार", "गुरूवार"],
    4: ["friday", "fri", "shukravar", "shukrawar", "shukravaar", "शुक्रवार"],
    5: ["saturday", "sat", "shanivar", "shaniwar", "shanivaar", "शनिवार"],
    6: ["sunday", "sun", "ravivar", "raviwar", "ravivaar", "itvaar", "itwar", "रविवार", "इतवार"],
}
_TODAY = {"today", "aaj", "आज", "abhi"}
_YESTERDAY = {"yesterday", "kal", "कल", "काल", "kaal"}
_DAY_BEFORE = {"parso", "parson", "परसों", "परसो", "परवा", "parva"}
_AGO = {"ago", "pehle", "pahle", "पहले", "पूर्वी", "आधी", "purvi"}
_DAY_WORDS = {"day", "days", "din", "दिन", "दिवस", "दिवसांपूर्वी", "दिवसापूर्वी"}
_DATE_WORDS = {"tarikh", "tareekh", "tarik", "तारीख", "तारीख़", "तारखेला", "तारीखला", "ko", "को", "la", "ला", "on", "the",
               "th", "st", "nd", "rd", "ki", "की", "ka", "का"}


class DateParseError(ValueError):
    pass


def _tokens(v: str) -> list[str]:
    return [t for t in re.split(r"[\s,.?!/\-]+", v) if t]


def _most_recent(day: int, month: int, today: date) -> date:
    """'15 September' without a year → the most recent 15 September on or before today."""
    for year in (today.year, today.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d <= today:
            return d
    raise DateParseError(f"invalid day {day} for month {month}")


def parse_date(value, today: date) -> date:
    """Resolve a spoken/typed day to a date. Raises DateParseError if it can't be understood."""
    if value is None:
        return today
    v = str(value).strip().lower().translate(_DEV_DIGITS)
    if not v:
        return today
    v = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", v)            # 15th → 15

    # ISO and numeric forms: 2026-09-15 · 20260915 · 15/9 · 15-09-2026 · 15.9.26
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v) or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", v)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError as e:
            raise DateParseError(str(e)) from e
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?", v)
    if m:                                                   # Indian order: day/month[/year]
        day, month = int(m[1]), int(m[2])
        try:
            if m[3]:
                year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
                return date(year, month, day)
            return _most_recent(day, month, today)
        except ValueError as e:
            raise DateParseError(str(e)) from e

    toks = _tokens(v)
    tokset = set(toks)
    if tokset & _TODAY and not tokset & _AGO:
        return today
    if tokset & _DAY_BEFORE:
        return today - timedelta(days=2)
    if tokset & _YESTERDAY:
        return today - timedelta(days=1)

    nums = [int(t) for t in toks if t.isdigit()]
    # "3 din pehle" / "3 days ago" / "3 दिवसांपूर्वी"
    if nums and (tokset & _AGO or any(t.endswith("पूर्वी") for t in toks)) and (tokset & _DAY_WORDS or any("दिवस" in t for t in toks)):
        n = nums[0]
        if n > 3660:
            raise DateParseError("too far back")
        return today - timedelta(days=n)

    # "15 September" / "Sep 15" / "15 सितंबर"
    month = next((_MONTH_LOOKUP[t] for t in toks if t in _MONTH_LOOKUP), None)
    if month and nums:
        day = next((n for n in nums if 1 <= n <= 31), None)
        year = next((n for n in nums if n >= 2000), None)
        if day:
            try:
                return date(year, month, day) if year else _most_recent(day, month, today)
            except ValueError as e:
                raise DateParseError(str(e)) from e

    # "2 tarikh" / "1 तारीख" → that day of this month (or last month if still ahead)
    if nums and (tokset & _DATE_WORDS or len(toks) == 1) and 1 <= nums[0] <= 31:
        day = nums[0]
        for back in range(0, 3):
            y, mo = today.year, today.month - back
            while mo < 1:
                mo += 12
                y -= 1
            try:
                d = date(y, mo, day)
            except ValueError:
                continue
            if d <= today:
                return d

    # weekday names → the most recent such day (today counts)
    for wd, names in WEEKDAY_ALIASES.items():
        if tokset & set(names) or any(n in v for n in names if len(n) > 4):
            return today - timedelta(days=(today.weekday() - wd) % 7)
    raise DateParseError(f"Could not understand date '{value}'")


def parse_amount(value) -> int | None:
    """Rupee amount from 500 / '₹1,000' / 'Rs. 500' / '500 rupees' / '५००'. None if not a usable number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return int(round(value))
    s = str(value or "").translate(_DEV_DIGITS).lower()
    s = re.sub(r"(₹|rs\.?|inr|rupees?|rupaye|rupaiye|रुपये|रुपए|रुपया|रु\.?)", " ", s)
    s = s.replace(",", "").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|hazaar|hazar|हज़ार|हजार)?", s)
    if not m:
        return None
    n = float(m[1]) * (1000 if m[2] else 1)
    return int(round(n)) if math.isfinite(n) else None


def clamp_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


HONORIFICS = {"ji", "jee", "bhai", "bhaiya", "bhaiyya", "tai", "didi", "sahab", "saab", "seth", "ka", "ki", "ko", "ke",
              "uncle", "aunty", "mr", "mrs", "shri", "smt", "जी", "भाई", "भैया", "ताई", "दीदी", "साहब", "का", "की", "को", "के",
              "चा", "ची", "ला", "शेठ", "काका", "मावशी"}


def name_tokens(name) -> list[str]:
    return [t for t in re.split(r"[\s,.?!]+", str(name or "").lower().strip()) if t and t not in HONORIFICS]


def is_valid_name(name) -> bool:
    """A real person's name: has letters once honorifics are removed, sensible length, no markup."""
    s = str(name or "").strip()
    if not s or len(s) > 60 or re.search(r"[<>{}\[\]\\/@#$%^*=|`~]", s):
        return False
    toks = name_tokens(s)
    return bool(toks) and any(ch.isalpha() for ch in "".join(toks))


def _candidates(r: dict, key: str) -> tuple[list[str], list[str]]:
    """(first-name-level candidates, other tokens) for a record, English + Devanagari + aliases."""
    name = str(r.get(key) or "").lower()
    hi = str(r.get(f"{key}_hi") or r.get("name_hi") or "").lower()
    firsts = [name, name.split()[0] if name else ""] + [a.lower() for a in r.get("aliases", []) or []]
    others = name.split()[1:]
    if hi:
        firsts += [hi, hi.split()[0]]
        others += hi.split()[1:]
    return [c for c in firsts if c], others


def match_records(query, records: list[dict], key: str = "customer_name") -> list[dict]:
    """Best matches for a spoken name. Full/first-name/alias matches score 2 per token, surname 1; only top scorers return."""
    q_tokens = name_tokens(query)
    q = " ".join(q_tokens)
    if not q:
        return []
    exact, scored = [], []
    for r in records:
        firsts, others = _candidates(r, key)
        if q in firsts:
            exact.append(r)
            continue
        score = sum(2 if tok in firsts else 1 if tok in others else 0 for tok in q_tokens)
        if score:
            scored.append((score, r))
    if exact:
        return exact
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [r for s, r in scored if s == best]


def suggest_records(query, records: list[dict], key: str = "customer_name", cutoff: float = 0.72) -> list[dict]:
    """Close spellings ('Sures', 'Sursh' → Suresh) — offered as 'did you mean', never acted on."""
    q_tokens = name_tokens(query)
    if not q_tokens:
        return []
    out = []
    for r in records:
        firsts, others = _candidates(r, key)
        pool = firsts + others
        best = max((difflib.SequenceMatcher(None, tok, c).ratio() for tok in q_tokens for c in pool), default=0)
        if best >= cutoff:
            out.append((best, r))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out[:3]]
