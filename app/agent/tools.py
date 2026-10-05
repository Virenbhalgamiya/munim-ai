"""Agent tools over the logged-in merchant's data (SQLite via app.db.store).

The merchant comes from the request context (store.current_merchant), never from tool arguments.

Every tool returns a JSON-serialisable dict. Amounts are integers in rupees; `*_display`
fields are pre-formatted in Indian style (₹1,23,456) so the LLM can read them aloud as-is.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from datetime import date as date_cls  # `date` is shadowed by daily_summary's argument
from typing import Any

from app import config
from app.db import store
from app.i18n import current_lang, date_display, join_names, name_for, t
from app.i18n import t as tr  # for functions whose loops use `t` as a variable
from app.parsing import (DateParseError, WEEKDAY_ALIASES, clamp_int, is_valid_name, match_records, parse_amount,
                         parse_date, suggest_records)

logger = logging.getLogger(__name__)

OVERDUE_DAYS = 10  # udhaar with no payment for more than this many days is "overdue"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
from app.hindi import CATEGORY_DEV, WEEKDAY_DEV as WEEKDAY_HI, has_devanagari, hi_date  # noqa: E402  (Devanagari display names)
_WEEKDAY_ALIASES = WEEKDAY_ALIASES
_HONORIFICS = {"ji", "bhai", "bhaiya", "tai", "didi", "sahab", "saab", "ka", "ki", "ko", "ke", "uncle", "aunty", "जी", "भाई"}


# ---------------------------------------------------------------- helpers
def fmt_inr(n: float | int) -> str:
    """Indian digit grouping: 123456 -> ₹1,23,456"""
    n = int(round(n))
    neg, s = n < 0, str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if len(head) > 2 else head
        s = f"{head},{tail}"
    return f"{'-' if neg else ''}₹{s}"


def _data() -> dict:
    return store.load_merchant_data(store.require_merchant())


def get_today() -> date:
    return store.today_for(store.require_merchant())


def _daily_totals(data: dict) -> dict[date, dict]:
    out: dict[date, dict] = defaultdict(lambda: {"total": 0, "count": 0})
    for t in data["transactions"]:
        d = date.fromisoformat(t["date"])
        out[d]["total"] += t["amount"]
        out[d]["count"] += 1
    return dict(out)


def resolve_date(value: str | None, today: date | None = None) -> date:
    """Spoken/typed day → date: aaj/kal/parso, weekdays (en/hi/mr), '15 September', '15/9', '2 tarikh', '3 din pehle'…"""
    return parse_date(value, today or get_today())


def _norm_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[\s,.?!]+", name.lower().strip()) if t and t not in _HONORIFICS]


def match_customer(query: str, records: list[dict], key: str = "customer_name") -> list[dict]:
    """Match on full name, first name, aliases (incl. Devanagari); first-name hits outrank surname hits."""
    return match_records(query, records, key)


def _not_found(query: str, records: list[dict], key: str, fallback_key: str, **fallback_kw) -> dict:
    """Not-found message, with a 'did you mean' when the name is a close misspelling (never acted on)."""
    lang = current_lang.get()
    sug = suggest_records(query, records, key)
    if sug:
        names = [name_for(r, lang, key=key) for r in sug]
        return {"message": t("tool.did_you_mean", name=query, sug=join_names(names, lang)),
                "did_you_mean": [r[key] for r in sug]}
    return {"message": t(fallback_key, **fallback_kw)}


def _ambiguous(matches: list[dict], key: str) -> dict:
    lang = current_lang.get()
    return {"candidates": [m[key] for m in matches],
            "message": t("tool.ambiguous", names=join_names([name_for(m, lang, key=key) for m in matches], lang))}


# ---------------------------------------------------------------- tools
def daily_summary(date: str | None = None) -> dict[str, Any]:  # noqa: A002 - name matches tool schema
    data = _data()
    today = get_today()
    lang = current_lang.get()
    try:
        target = resolve_date(date, today)
    except DateParseError:
        return {"error": "date_not_understood", "query": str(date), "message": tr("tool.bad_date", d=date)}
    daily = _daily_totals(data)
    if target > today:
        return {"error": "future_date", "date": target.isoformat(), "message": tr("tool.future_date", d=date_display(target, lang))}
    if target not in daily:
        start, end = data["period"]["start"], data["period"]["end"]
        fmt_d = lambda s: date_display(date_cls.fromisoformat(s), lang) if s else "—"  # noqa: E731
        return {"error": "no_data_for_date", "date": target.isoformat(),
                "data_available_from": start, "data_available_to": end,
                "message": tr("tool.no_data_for", d=date_display(target, lang), start=fmt_d(start), end=fmt_d(end))}

    weekday = WEEKDAYS[target.weekday()]
    day = daily[target]

    # same weekday, previous 4 occurrences
    prev_same = [target - timedelta(weeks=i) for i in range(1, 5) if (target - timedelta(weeks=i)) in daily]
    same_avg = sum(daily[d]["total"] for d in prev_same) / len(prev_same) if prev_same else None

    # all days in the 28 days before target (baseline)
    window = [target - timedelta(days=i) for i in range(1, 29) if (target - timedelta(days=i)) in daily]
    overall_avg = sum(daily[d]["total"] for d in window) / len(window) if window else None

    # weekday pattern over the last 8 weeks up to target
    by_wd: dict[str, list[int]] = defaultdict(list)
    for d, v in daily.items():
        if target - timedelta(days=56) < d <= target:
            by_wd[WEEKDAYS[d.weekday()]].append(v["total"])
    pattern = {wd: round(sum(v) / len(v)) for wd, v in ((wd, by_wd[wd]) for wd in WEEKDAYS) if v}
    weakest = min(pattern, key=pattern.get)
    others = [v for k, v in pattern.items() if k != weakest]
    weakest_gap = 1 - pattern[weakest] / (sum(others) / len(others))

    # category mix for the day
    cat_totals: dict[str, int] = defaultdict(int)
    for t in data["transactions"]:
        if t["date"] == target.isoformat():
            for it in t["items"]:
                cat_totals[it["category"]] += it["price"] * it["qty"]

    def pct(a, b):
        return round((a / b - 1) * 100, 1) if b else None

    result: dict[str, Any] = {
        "date": target.isoformat(),
        "is_today": target == today,
        "weekday": weekday,
        "weekday_hi": WEEKDAY_HI[weekday],
        "total_sales": day["total"],
        "total_sales_display": fmt_inr(day["total"]),
        "transaction_count": day["count"],
        "avg_basket": round(day["total"] / day["count"]),
        "same_weekday_avg_last_4": round(same_avg) if same_avg else None,
        "pct_vs_same_weekday_avg": pct(day["total"], same_avg),
        "overall_daily_avg_last_28_days": round(overall_avg) if overall_avg else None,
        "pct_vs_overall_daily_avg": pct(day["total"], overall_avg),
        "weekday_pattern_avg_sales": pattern,
        "weakest_weekday": weakest,
        "weakest_weekday_pct_below_other_days": round(weakest_gap * 100, 1),
        "top_category_today": max(cat_totals, key=cat_totals.get) if cat_totals else None,
    }

    # Candidate proactive insights, most relevant first. The agent should mention ONE.
    insights: list[dict] = []
    gap_pct = round(weakest_gap * 100)
    if weekday == weakest and weakest_gap >= 0.15:
        insights.append({
            "type": "weekday_dip", "weekday": weekday, "weekday_hi": WEEKDAY_HI[weekday], "pct_below_other_days": gap_pct,
            "text": f"{weekday} ({WEEKDAY_HI[weekday]}) is consistently the weakest day — about {gap_pct}% below other "
                    f"days on average (avg {fmt_inr(pattern[weakest])} vs {fmt_inr(sum(others) / len(others))}). "
                    f"Suggest a {WEEKDAY_HI[weekday]} offer/combo.",
        })
    elif result["pct_vs_overall_daily_avg"] is not None and result["pct_vs_overall_daily_avg"] <= -15:
        insights.append({"type": "below_average", "pct": abs(result["pct_vs_overall_daily_avg"]),
                         "text": f"Sales were {abs(result['pct_vs_overall_daily_avg'])}% below the recent daily average."})
    lapsed = lapsed_customers()
    if lapsed["lapsed_count"]:
        names = [c["customer_name"] for c in lapsed["lapsed_customers"]]
        insights.append({
            "type": "lapsed_customers", "names": names, "weekly_spend_at_risk": lapsed["weekly_spend_at_risk_total"],
            "text": f"{len(names)} regular customers have stopped coming ({', '.join(names)}) — about "
                    f"{lapsed['weekly_spend_at_risk_display']}/week of business at risk.",
        })
    overdue = [u for u in udhaar_lookup()["customers"] if u["is_overdue"]]
    if overdue:
        u = overdue[0]
        insights.append({
            "type": "overdue_udhaar", "customer_name": u["customer_name"], "outstanding": u["outstanding"],
            "days_since_last_payment": u["days_since_last_payment"],
            "text": f"{u['customer_name']} owes {u['outstanding_display']} udhaar, no payment for "
                    f"{u['days_since_last_payment']} days — could send a reminder.",
        })
    if weekday != weakest:
        insights.append({"type": "weakest_weekday", "weekday": weakest, "weekday_hi": WEEKDAY_HI[weakest],
                         "pct_below_other_days": gap_pct,
                         "text": f"{weakest} ({WEEKDAY_HI[weakest]}) is your weakest day, ~{gap_pct}% below others."})
    result["insights"] = insights
    return result


def _udhaar_view(u: dict, today: date) -> dict:
    # No payment yet -> count from when the credit started.
    last = date.fromisoformat(u["last_payment_date"] or u["credit_since"] or today.isoformat())
    days = max(0, (today - last).days)
    return {
        "customer_name": u["customer_name"],
        "customer_name_hi": u.get("customer_name_hi") or u["customer_name"],
        "outstanding": u["outstanding"],
        "outstanding_display": fmt_inr(u["outstanding"]),
        "credit_since": u["credit_since"],
        "last_payment_date": u["last_payment_date"],
        "last_payment_amount": u["last_payment_amount"],
        "days_since_last_payment": days,
        "is_overdue": days > OVERDUE_DAYS,
        "note": u.get("note", ""),
    }


def udhaar_lookup(customer_name: str | None = None) -> dict[str, Any]:
    customer_name = str(customer_name).strip() if customer_name is not None else None
    if customer_name and not name_tokens_ok(customer_name):
        customer_name = None          # only honorifics ("ji") → list everyone
    data = _data()
    today = get_today()
    ledger = [u for u in data["udhaar"] if u["outstanding"] > 0]
    if customer_name:
        matches = match_customer(customer_name, ledger)
        if not matches:
            return {"found": False, "query": customer_name,
                    **_not_found(customer_name, ledger, "customer_name", "tool.no_udhaar", name=customer_name),
                    "customers_with_udhaar": [u["customer_name"] for u in ledger]}
        return {"found": True, "query": customer_name, "customers": [_udhaar_view(u, today) for u in matches]}
    views = sorted((_udhaar_view(u, today) for u in ledger),
                   key=lambda v: (not v["is_overdue"], -v["days_since_last_payment"] if v["is_overdue"] else -v["outstanding"]))
    total = sum(v["outstanding"] for v in views)
    return {
        "found": True,
        "customer_count": len(views),
        "total_outstanding": total,
        "total_outstanding_display": fmt_inr(total),
        "overdue_count": sum(v["is_overdue"] for v in views),
        "overdue_rule": f"no payment for more than {OVERDUE_DAYS} days",
        "customers": views,
    }


def lapsed_customers(days_threshold: int = 10) -> dict[str, Any]:
    days_threshold = clamp_int(days_threshold, 10, 1, 60)
    data = _data()
    today = get_today()
    visits: dict[str, set[date]] = defaultdict(set)
    spend: dict[str, int] = defaultdict(int)
    for t in data["transactions"]:
        if t["customer_type"] != "regular":
            continue
        visits[t["customer_name"]].add(date.fromisoformat(t["date"]))
        spend[t["customer_name"]] += t["amount"]

    udhaar_names = {u["customer_name"]: u["outstanding"] for u in data["udhaar"]}
    hi_names = {c["name"]: c.get("name_hi") or c["name"] for c in data.get("customers", [])}
    lapsed, active = [], []
    for name, ds in visits.items():
        first, last = min(ds), max(ds)
        weeks = max(1.0, ((last - first).days + 1) / 7)
        per_week = len(ds) / weeks
        if len(ds) < 6 or per_week < 1.5:
            continue  # was never a real regular
        days_since = (today - last).days
        row = {
            "customer_name": name,
            "customer_name_hi": hi_names.get(name, name),
            "last_visit": last.isoformat(),
            "days_since_last_visit": days_since,
            "historical_visits_per_week": round(per_week, 1),
            "total_visits": len(ds),
            "avg_spend_per_visit": round(spend[name] / len(ds)),
            "weekly_spend_at_risk": round(per_week * spend[name] / len(ds)),
        }
        if name in udhaar_names:
            row["udhaar_outstanding"] = udhaar_names[name]
        (lapsed if days_since > days_threshold else active).append(row)
    lapsed.sort(key=lambda r: -r["weekly_spend_at_risk"])
    at_risk = sum(r["weekly_spend_at_risk"] for r in lapsed)
    return {
        "days_threshold": days_threshold,
        "as_of": today.isoformat(),
        "lapsed_count": len(lapsed),
        "lapsed_customers": lapsed,
        "weekly_spend_at_risk_total": at_risk,
        "weekly_spend_at_risk_display": fmt_inr(at_risk),
        "active_regulars_count": len(active),
    }


def product_performance(days: int = 14) -> dict[str, Any]:
    days = clamp_int(days, 14, 1, 30)
    data = _data()
    today = get_today()
    cur_start, prev_start = today - timedelta(days=days - 1), today - timedelta(days=2 * days - 1)
    cur_cat, prev_cat = defaultdict(int), defaultdict(int)
    units: dict[str, int] = defaultdict(int)
    for t in data["transactions"]:
        d = date.fromisoformat(t["date"])
        for it in t["items"]:
            amt = it["price"] * it["qty"]
            if d >= cur_start:
                cur_cat[it["category"]] += amt
                units[it["name"]] += it["qty"]
            elif d >= prev_start:
                prev_cat[it["category"]] += amt
    total = sum(cur_cat.values()) or 1
    cats = sorted(
        ({"category": c, "category_hi": CATEGORY_DEV.get(c, c), "sales": v, "sales_display": fmt_inr(v), "share_pct": round(v / total * 100, 1),
          "pct_change_vs_previous_period": round((v / prev_cat[c] - 1) * 100, 1) if prev_cat.get(c) else None}
         for c, v in cur_cat.items()),
        key=lambda r: r["sales"],
    )
    all_products = {p["name"]: p for p in data["catalog"]}
    slow = sorted(({"product": n, "category": all_products[n]["category"], "units_sold": units.get(n, 0)}
                   for n in all_products), key=lambda r: r["units_sold"])[:5]
    return {"period_days": days, "from": cur_start.isoformat(), "to": today.isoformat(),
            "categories_weakest_first": cats, "slowest_products": slow}


def build_reminder_message(record: dict, store_name: str, owner_name: str) -> str:
    """The message the customer receives, in Devanagari Hindi."""
    first = (record.get("customer_name_hi") or record["customer_name"]).split()[0]
    paid = record.get("last_payment_date")
    since = (f"आखिरी भुगतान {hi_date(date.fromisoformat(paid))}" if paid
             else f"{hi_date(date.fromisoformat(record['credit_since']))} से")
    return (f"नमस्ते {first} जी, {store_name} से {owner_name}। आपका {fmt_inr(record['outstanding'])} का उधार "
            f"बाकी है ({since})। सुविधा हो तो Paytm UPI से भेज दीजिए। धन्यवाद!")


def _deliver(rec: dict, text: str, merchant: dict) -> dict:
    """Send via the Telegram Bot API. The synthetic customers have no Telegram chat id, so the message
    goes to the merchant's chat (TELEGRAM_CHAT_ID) with a header naming the customer."""
    from app.services.telegram_service import get_telegram_service

    chat_id = rec.get("telegram_chat_id") or merchant.get("telegram_chat_id") or config.TELEGRAM_CHAT_ID
    who = rec.get("customer_name_hi") or rec["customer_name"]
    return get_telegram_service().send_message(f"📩 उधार रिमाइंडर → {who}\n\n{text}", chat_id)


def send_reminder(customer_name: str, confirmed: bool = False, message: str | None = None,
                  channel: str = "telegram") -> dict[str, Any]:
    mid = store.require_merchant()
    if channel != "telegram":
        return {"status": "error", "sent": False, "message": t("tool.telegram_only")}
    customer_name = str(customer_name or "").strip()
    if not is_valid_name(customer_name):
        return {"status": "error", "sent": False, "message": t("tool.need_name")}
    data = _data()
    owing = [u for u in data["udhaar"] if u["outstanding"] > 0]
    matches = match_customer(customer_name, owing)
    if not matches:
        return {"status": "error", "sent": False, **_not_found(customer_name, owing, "customer_name", "tool.no_udhaar", name=customer_name)}
    if len(matches) > 1:
        return {"status": "ambiguous", "sent": False, **_ambiguous(matches, "customer_name")}
    rec = matches[0]
    merchant = store.get_merchant(mid)
    text = (message or build_reminder_message(rec, merchant.get("store_name_hi") or merchant["store_name"],
                                              merchant.get("owner_name_hi") or merchant["owner_name"]))[:600]
    base = {"customer_name": rec["customer_name"], "customer_name_hi": rec.get("customer_name_hi") or rec["customer_name"],
            "outstanding": rec["outstanding"],
            "outstanding_display": fmt_inr(rec["outstanding"]), "channel": "telegram", "reminder_message": text}

    # Business rules apply to previews too, so the merchant hears about them before confirming.
    if rec.get("reminder_opt_out"):
        return {**base, "status": "blocked_opt_out", "sent": False,
                "message": t("tool.opt_out", name=name_for(rec, current_lang.get()))}
    last = store.last_sent_reminder(mid, rec["customer_id"])
    if last and store.hours_since(last["created_at"]) < config.REMINDER_COOLDOWN_HOURS:
        hrs = round(store.hours_since(last["created_at"]))
        return {**base, "status": "blocked_cooldown", "sent": False, "last_reminder_hours_ago": hrs,
                "message": t("tool.cooldown", name=name_for(rec, current_lang.get()),
                             when=t("tool.just_now") if hrs < 1 else t("tool.hours_ago", h=hrs), h=config.REMINDER_COOLDOWN_HOURS)}

    if not config.telegram_configured():
        return {**base, "status": "not_configured", "sent": False,
                "message": t("tool.tg_not_set")}

    if not confirmed:
        return {**base, "status": "confirmation_required", "sent": False,
                "instruction": "NOT SENT. Ask the merchant to confirm. Call again with confirmed=true only after they clearly say yes in their next message."}

    # Reserve the send atomically, so two confirmations at the same moment can't both message the customer.
    slot = store.claim_reminder_slot(mid, rec["customer_id"], "telegram", text, config.REMINDER_COOLDOWN_HOURS)
    if slot is None:
        return {**base, "status": "blocked_cooldown", "sent": False,
                "message": t("tool.cooldown", name=name_for(rec, current_lang.get()), when=t("tool.just_now"),
                             h=config.REMINDER_COOLDOWN_HOURS)}
    try:
        result = _deliver(rec, text, merchant)
    except Exception as e:
        store.finish_reminder(mid, slot, rec["customer_id"], "telegram", "failed", None, str(e)[:300])
        return {**base, "status": "error", "sent": False, "message": t("tool.reminder_failed", e=e)}
    demo = bool(result.get("demo"))
    store.finish_reminder(mid, slot, rec["customer_id"], "telegram", "sent", "telegram-demo" if demo else "telegram", None)
    return {**base, "status": "sent", "sent": True, "via": "telegram-demo" if demo else "telegram",
            "telegram_message_id": result.get("message_id")}


def record_udhaar(customer_name: str, amount: int, kind: str = "payment", note: str = "",
                  confirmed: bool = False) -> dict[str, Any]:
    """kind='payment': customer paid back. kind='credit': customer took goods on udhaar."""
    from app.db.database import transaction

    mid = store.require_merchant()
    kind = _KIND_SYNONYMS.get(str(kind or "").strip().lower(), str(kind or "").strip().lower())
    if kind not in ("payment", "credit"):
        return {"status": "error", "saved": False, "message": t("tool.kind")}
    parsed = parse_amount(amount)
    if parsed is None:
        return {"status": "error", "saved": False, "message": t("tool.bad_amount", a=amount)}
    amount = parsed
    if amount <= 0 or amount > config.MAX_UDHAAR_ENTRY:
        return {"status": "error", "saved": False,
                "message": t("tool.amount_range", max=fmt_inr(config.MAX_UDHAAR_ENTRY))}
    name = str(customer_name or "").strip()
    if not is_valid_name(name):
        return {"status": "error", "saved": False, "message": t("tool.need_name")}

    data = _data()
    balances = {u["customer_id"]: u for u in data["udhaar"]}
    matches = match_customer(name, data["customers"], key="name")
    if len(matches) > 1:
        return {"status": "ambiguous", "saved": False, **_ambiguous(matches, "name")}
    customer = matches[0] if matches else None
    current = balances.get(customer["id"], {}).get("outstanding", 0) if customer else 0

    if customer is None:
        # A close misspelling of an existing customer must never silently open a second khata.
        near = _not_found(name, data["customers"], "name", "tool.no_customer", name=name)
        if kind == "payment" or "did_you_mean" in near:
            return {"status": "error", "saved": False, **near}
    if kind == "payment" and amount > current:
        return {"status": "error", "saved": False, "outstanding": current,
                "message": t("tool.overpay", name=name_for(customer, current_lang.get(), key="name"), cur=fmt_inr(current), amt=fmt_inr(amount))}

    new_balance = current - amount if kind == "payment" else current + amount
    base = {"customer_name": customer["name"] if customer else name.title(),
            "customer_name_hi": (customer.get("name_hi") or customer["name"]) if customer else name,
            "kind": kind, "amount": amount,
            "amount_display": fmt_inr(amount), "outstanding_before": current,
            "outstanding_after": new_balance, "outstanding_after_display": fmt_inr(new_balance),
            "new_customer": customer is None, "note": (note or "")[:200]}

    if not confirmed:
        return {**base, "status": "confirmation_required", "saved": False,
                "instruction": "NOT SAVED. Read back the entry and ask the merchant to confirm. Call again with "
                               "confirmed=true only after they clearly say yes in their next message."}

    # Check-and-write in ONE locked transaction (BEGIN IMMEDIATE): two payments confirmed at the same moment
    # can never both pass the balance check and overdraw the khata.
    with transaction() as conn:
        if customer is None:
            customer = store.create_customer(mid, name, conn=conn)
            store.audit(mid, "merchant", "customer.created", {"customer_id": customer["id"], "name": customer["name"]}, conn)
            fresh = 0
        else:
            fresh = store.balance_of(mid, customer["id"], conn)
        if kind == "payment" and amount > fresh:
            overdraw = True
        else:
            overdraw = False
            store.add_udhaar_entry(mid, customer["id"], kind, amount, get_today().isoformat(), base["note"], "voice",
                                   "merchant", conn=conn)
    who = customer.get("name_hi") or customer["name"]
    if overdraw:
        return {**base, "status": "error", "saved": False, "outstanding": fresh,
                "message": t("tool.overpay", name=name_for(customer, current_lang.get(), key="name"), cur=fmt_inr(fresh),
                             amt=fmt_inr(amount))}
    after = fresh - amount if kind == "payment" else fresh + amount
    return {**base, "customer_name": customer["name"], "customer_name_hi": who, "outstanding_before": fresh,
            "outstanding_after": after, "outstanding_after_display": fmt_inr(after), "status": "saved", "saved": True}


def pending_upi_payments() -> dict[str, Any]:
    """UPI payments received from customers who owe udhaar, waiting for the merchant to say: repayment or purchase."""
    from app import payments
    items = payments.list_pending(store.require_merchant())
    return {"count": len(items), "payments": [
        {"payment_id": p["id"], "customer_name": p["customer_name"], "customer_name_hi": p["customer_name_hi"],
         "amount": p["amount"], "amount_display": p["amount_display"], "outstanding": p["outstanding"],
         "outstanding_display": p["outstanding_display"], "time": p["time"]} for p in items]}


def link_upi_payment(customer_name: str, confirmed: bool = False, as_purchase: bool = False) -> dict[str, Any]:
    """Apply the merchant's decision on a pending UPI payment: repayment (khata) or purchase (sale)."""
    from app import payments
    mid = store.require_merchant()
    customer_name = str(customer_name or "").strip()
    if not is_valid_name(customer_name):
        return {"status": "error", "saved": False, "message": t("tool.need_name")}
    pending = payments.list_pending(mid)
    hits = match_customer(customer_name, pending, key="customer_name")
    if not hits:
        return {"status": "error", "saved": False,
                **_not_found(customer_name, pending, "customer_name", "tool.no_pending_upi", name=customer_name)}
    p = hits[0]
    base = {"payment_id": p["id"], "customer_name": p["customer_name"], "customer_name_hi": p["customer_name_hi"],
            "amount_display": p["amount_display"], "outstanding_display": p["outstanding_display"],
            "decision": "purchase" if as_purchase else "repayment"}
    if not confirmed:
        after = p["outstanding"] - (0 if as_purchase else p["would_link"])
        return {**base, "status": "confirmation_required", "saved": False, "outstanding_after_display": fmt_inr(after),
                "instruction": "NOT SAVED. Ask the merchant to confirm; call again with confirmed=true only after a clear yes."}
    try:
        out = payments.dismiss_as_purchase(mid, p["id"]) if as_purchase else payments.link_as_repayment(mid, p["id"])
    except payments.PaymentError as e:
        return {**base, "status": "error", "saved": False, "message": t("tool.already_decided", e=e)}
    return {**base, "status": "saved", "saved": True,
            "linked_display": out.get("linked_display"), "outstanding_after_display": out.get("outstanding_after_display")}


_KIND_SYNONYMS = {
    "paid": "payment", "pay": "payment", "repayment": "payment", "jama": "payment", "diya": "payment", "diye": "payment",
    "जमा": "payment", "दिए": "payment", "दिया": "payment", "udhaar": "credit", "udhar": "credit", "liya": "credit",
    "liye": "credit", "उधार": "credit", "लिया": "credit", "लिए": "credit", "debit": "credit",
}


def name_tokens_ok(name: str) -> bool:
    from app.parsing import name_tokens
    return bool(name_tokens(name))


TOOL_FUNCTIONS = {
    "daily_summary": daily_summary,
    "udhaar_lookup": udhaar_lookup,
    "lapsed_customers": lapsed_customers,
    "product_performance": product_performance,
    "send_reminder": send_reminder,
    "record_udhaar": record_udhaar,
    "pending_upi_payments": pending_upi_payments,
    "link_upi_payment": link_upi_payment,
}

# Tools that change data or contact customers: the agent enforces a two-turn confirmation.
CONFIRM_TOOLS = {"send_reminder", "record_udhaar", "link_upi_payment"}


def execute_tool(name: str, tool_input: dict) -> dict:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'"}
    try:
        return fn(**(tool_input or {}))
    except TypeError as e:  # bad/missing arguments from the LLM
        return {"error": f"Invalid arguments for {name}: {e}"}
    except Exception as e:  # surface errors to the LLM rather than crash the turn
        logger.exception("tool %s failed", name)
        return {"error": f"{type(e).__name__}: {e}"}
