"""Proactive insight engine for the dashboard, in हिंदी / English / मराठी.

Everything is computed from the merchant's data in code (no AI call): instant, free, and exact.
The assistant uses the same tools, so dashboard and voice always agree.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date, timedelta

from app import i18n
from app.agent import tools
from app.agent.tools import WEEKDAYS, fmt_inr
from app.db import store
from app.i18n import t

SEVERITY_ORDER = {"critical": 0, "warning": 1, "opportunity": 2, "info": 3}
_dash_cache: dict[tuple, dict] = {}
_dash_lock = threading.Lock()


def _first(name: str) -> str:
    return (name or "").split()[0]


def _owner(data: dict, lang: str) -> str:
    return data["store"]["owner_name"] if lang == "en" else data["store"]["owner_name_hi"]


def _store_name(data: dict, lang: str) -> str:
    return data["store"]["store_name"] if lang == "en" else data["store"]["store_name_hi"]


def _weekday_rows(pattern: dict, lang: str) -> list[dict]:
    return [{"weekday": wd, "weekday_hi": i18n.WEEKDAY[lang][wd], "short": i18n.WEEKDAY_SHORT[lang][wd],
             "avg": pattern.get(wd, 0)} for wd in WEEKDAYS]


def build_dashboard(merchant_id: str, lang: str = "hi") -> dict:
    """Cached per language until the merchant's data changes or the day changes."""
    from app import config
    lang = i18n.lang_or_default(lang)
    with store.merchant_scope(merchant_id):
        key = (str(config.DB_PATH), merchant_id, lang, tools.get_today().isoformat(), store.data_version(merchant_id))
    with _dash_lock:
        hit = _dash_cache.get(key)
    if hit is not None:
        return hit
    result = _build(merchant_id, lang)
    with _dash_lock:
        for k in [k for k in _dash_cache if k[:3] == key[:3]]:
            del _dash_cache[k]
        _dash_cache[key] = result
    return result


def _empty(data: dict, today: date, khata: dict, pending: list, lang: str) -> dict:
    overdue = [c for c in khata.get("customers", []) if c["is_overdue"]]
    owner = _owner(data, lang)
    insights = [{
        "id": "overdue", "severity": "critical", "icon": "⏰",
        "title": t("overdue.title", lang, name=i18n.name_for(u, lang), d=u["days_since_last_payment"]),
        "detail": t("overdue.detail_none", lang, amt=u["outstanding_display"]), "impact": u["outstanding_display"],
        "impact_label": t("overdue.impact", lang),
        "action": {"label": t("overdue.action", lang), "question": t("overdue.q", lang, first=_first(i18n.name_for(u, lang)))},
    } for u in overdue[:3]]
    return {
        "lang": lang,
        "greeting": {"owner": owner, "store": _store_name(data, lang), "date": today.isoformat(),
                     "date_display": i18n.date_display(today, lang)},
        "kpis": [
            {"id": "sales", "label": t("kpi.sales", lang), "display": "₹0", "value": 0, "sub": t("kpi.empty.sales", lang)},
            {"id": "payments", "label": t("kpi.payments", lang), "display": "0", "value": 0},
            {"id": "udhaar", "label": t("kpi.udhaar", lang), "display": khata.get("total_outstanding_display", "₹0"),
             "value": khata.get("total_outstanding", 0),
             "sub": t("kpi.udhaar.sub", lang, n=khata.get("customer_count", 0), o=len(overdue)), "status": "warning" if overdue else None},
            {"id": "risk", "label": t("kpi.risk", lang), "display": "₹0", "value": 0, "sub": t("kpi.empty.risk", lang)},
        ],
        "insights": insights,
        "briefing": t("brief.empty", lang, owner=owner),
        "sales_28d": [{"date": (today - timedelta(days=i)).isoformat(), "label": i18n.date_label(today - timedelta(days=i), lang),
                       "weekday": WEEKDAYS[(today - timedelta(days=i)).weekday()], "total": 0} for i in range(27, -1, -1)],
        "weekday_avg": _weekday_rows({}, lang), "weakest_weekday": None, "weakest_weekday_hi": None, "weakest_gap_pct": 0,
        "categories": [], "slow_products": [], "khata": khata, "lapsed": [], "pending_payments": pending,
    }


def _build(merchant_id: str, lang: str) -> dict:
    from app import payments
    with store.merchant_scope(merchant_id):
        data = store.load_merchant_data(merchant_id)
        today = tools.get_today()
        summary = tools.daily_summary()
        khata = tools.udhaar_lookup()
        lapsed = tools.lapsed_customers()
        products = tools.product_performance(14)
    pending = payments.list_pending(merchant_id)
    if "error" in summary:                      # new merchant / no sales recorded yet
        return _empty(data, today, khata, pending, lang)

    owner = _owner(data, lang)
    daily: dict[date, int] = defaultdict(int)
    hourly: dict[int, int] = defaultdict(int)
    spend_30d: dict[str, int] = defaultdict(int)
    cat_by_customer: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tx in data["transactions"]:
        d = date.fromisoformat(tx["date"])
        daily[d] += tx["amount"]
        if today - d < timedelta(days=28):
            hourly[int(tx["time"][:2])] += tx["amount"]
        if tx["customer_type"] == "regular":
            if today - d < timedelta(days=30):
                spend_30d[tx["customer_name"]] += tx["amount"]
            for it in tx["items"]:
                cat_by_customer[tx["customer_name"]][it["category"]] += it["price"] * it["qty"]
    customers = {c["name"]: c for c in data["customers"]}

    def nm(latin: str) -> str:
        return i18n.name_for(customers.get(latin, {"name": latin}), lang, key="name")

    sales_28d = [{"date": (today - timedelta(days=i)).isoformat(), "label": i18n.date_label(today - timedelta(days=i), lang),
                  "weekday": WEEKDAYS[(today - timedelta(days=i)).weekday()],
                  "total": daily.get(today - timedelta(days=i), 0)} for i in range(27, -1, -1)]
    pattern = summary.get("weekday_pattern_avg_sales", {})
    weekday_avg = _weekday_rows(pattern, lang)
    weakest = summary.get("weakest_weekday")
    gap_pct = round(summary.get("weakest_weekday_pct_below_other_days", 0))
    others = [w["avg"] for w in weekday_avg if w["weekday"] != weakest and w["avg"]]
    weakest_avg = pattern.get(weakest, 0)
    others_avg = sum(others) / len(others) if others else 0
    wd_today = i18n.WEEKDAY[lang][summary["weekday"]]
    per_week = t("per_week", lang)

    # ---------------------------------------------------------------- KPI tiles
    overdue = [c for c in khata.get("customers", []) if c["is_overdue"]]
    kpis = [
        {"id": "sales", "label": t("kpi.sales", lang), "display": summary["total_sales_display"], "value": summary["total_sales"],
         "delta_pct": summary.get("pct_vs_same_weekday_avg"), "delta_label": t("kpi.sales.delta", lang, wd=wd_today),
         "trend": [s["total"] for s in sales_28d[-14:]], "trend_label": t("kpi.sales.trend", lang)},
        {"id": "payments", "label": t("kpi.payments", lang), "display": str(summary["transaction_count"]),
         "value": summary["transaction_count"], "sub": t("kpi.payments.sub", lang, v=fmt_inr(summary["avg_basket"]))},
        {"id": "udhaar", "label": t("kpi.udhaar", lang), "display": khata.get("total_outstanding_display", "₹0"),
         "value": khata.get("total_outstanding", 0),
         "sub": t("kpi.udhaar.sub", lang, n=khata.get("customer_count", 0), o=len(overdue)), "status": "warning" if overdue else None},
        {"id": "risk", "label": t("kpi.risk", lang), "display": lapsed["weekly_spend_at_risk_display"],
         "value": lapsed["weekly_spend_at_risk_total"],
         "sub": t("kpi.risk.some", lang, n=lapsed["lapsed_count"]) if lapsed["lapsed_count"] else t("kpi.risk.none", lang),
         "status": "critical" if lapsed["lapsed_count"] else None},
    ]

    # ---------------------------------------------------------------- insight cards
    insights: list[dict] = []
    if lapsed["lapsed_count"]:
        names = [_first(i18n.name_for(c, lang)) for c in lapsed["lapsed_customers"]]
        days = [c["days_since_last_visit"] for c in lapsed["lapsed_customers"]]
        insights.append({
            "id": "lapsed", "severity": "critical", "icon": "👥",
            "title": t("lapsed.title", lang, n=lapsed["lapsed_count"]),
            "detail": t("lapsed.detail", lang, names=i18n.join_names(names, lang), a=min(days), b=max(days)),
            "impact": lapsed["weekly_spend_at_risk_display"] + per_week, "impact_label": t("lapsed.impact", lang),
            "action": {"label": t("lapsed.action", lang), "question": t("lapsed.q", lang)},
        })
    if overdue:
        u = overdue[0]
        who = i18n.name_for(u, lang)
        insights.append({
            "id": "overdue", "severity": "critical", "icon": "⏰",
            "title": t("overdue.title", lang, name=who, d=u["days_since_last_payment"]),
            "detail": (t("overdue.detail", lang, amt=u["outstanding_display"],
                         date=i18n.date_label(date.fromisoformat(u["last_payment_date"]), lang))
                       if u["last_payment_date"] else t("overdue.detail_none", lang, amt=u["outstanding_display"])),
            "impact": u["outstanding_display"], "impact_label": t("overdue.impact", lang),
            "action": {"label": t("overdue.action", lang), "question": t("overdue.q", lang, first=_first(who))},
        })
    if weakest and gap_pct >= 15:
        wk = i18n.WEEKDAY[lang][weakest]
        insights.append({
            "id": "weekday_dip", "severity": "warning", "icon": "📉",
            "title": t("dip.title", lang, wd=wk, p=gap_pct),
            "detail": t("dip.detail", lang, wd=wk, a=fmt_inr(weakest_avg), b=fmt_inr(others_avg)),
            "impact": f"+{fmt_inr(round(others_avg - weakest_avg))}{per_week}", "impact_label": t("dip.impact", lang),
            "action": {"label": t("dip.action", lang, wd=wk), "question": t("dip.q", lang, wd=wk)},
        })
    cats = {c["category"]: c for c in products["categories_weakest_first"]}
    dropping = [c for c in cats.values() if (c.get("pct_change_vs_previous_period") or 0) <= -10]
    if dropping:
        c = min(dropping, key=lambda x: x["pct_change_vs_previous_period"])
        lost = round(c["sales"] / (1 + c["pct_change_vs_previous_period"] / 100) - c["sales"])
        link = ""
        if lapsed["lapsed_count"]:
            gone = [x["customer_name"] for x in lapsed["lapsed_customers"]]
            share = [cat_by_customer[n].get(c["category"], 0) / max(1, sum(cat_by_customer[n].values())) for n in gone]
            if share and sum(share) / len(share) >= 0.35:
                link = t("drop.link", lang)
        insights.append({
            "id": "category_drop", "severity": "warning", "icon": "🛒",
            "title": t("drop.title", lang, cat=i18n.CATEGORY[lang].get(c["category"], c["category"]),
                       p=abs(round(c["pct_change_vs_previous_period"]))),
            "detail": t("drop.detail", lang, s=c["sales_display"], lost=fmt_inr(lost), link=link),
            "impact": f"−{fmt_inr(lost)}", "impact_label": t("drop.impact", lang),
            "action": {"label": t("drop.action", lang), "question": t("drop.q", lang)},
        })
    slow = products["slowest_products"][0] if products["slowest_products"] else None
    if slow and slow["units_sold"] <= 5:
        insights.append({
            "id": "slow_stock", "severity": "opportunity", "icon": "📦",
            "title": t("slow.title", lang, p=slow["product"]), "detail": t("slow.detail", lang, u=slow["units_sold"]),
            "impact": t("slow.units", lang, u=slow["units_sold"]), "impact_label": t("slow.impact", lang),
            "action": {"label": t("slow.action", lang), "question": t("drop.q", lang)},
        })
    if hourly:
        total = sum(hourly.values())
        best = max(range(7, 21), key=lambda h: hourly.get(h, 0) + hourly.get(h + 1, 0))
        share = round((hourly.get(best, 0) + hourly.get(best + 1, 0)) / total * 100)
        end = i18n.hour_label(best + 2, lang) if lang == "en" else str((best + 1) % 12 + 1)
        insights.append({
            "id": "peak_hours", "severity": "opportunity", "icon": "⚡",
            "title": t("peak.title", lang, start=i18n.hour_label(best, lang), end=end),
            "detail": t("peak.detail", lang, s=share), "impact": f"{share}%", "impact_label": t("peak.impact", lang), "action": None,
        })
    if spend_30d:
        top = max(spend_30d, key=spend_30d.get)
        insights.append({
            "id": "top_customer", "severity": "info", "icon": "⭐",
            "title": t("top.title", lang, name=nm(top)), "detail": t("top.detail", lang, amt=fmt_inr(spend_30d[top])),
            "impact": fmt_inr(spend_30d[top]), "impact_label": t("top.impact", lang), "action": None,
        })
    insights.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])

    # ---------------------------------------------------------------- spoken briefing
    head = t("brief.head", lang, owner=owner, s=summary["total_sales_display"], n=summary["transaction_count"])
    delta = summary.get("pct_vs_same_weekday_avg")
    if delta is not None:
        head += t("brief.delta", lang, wd=wd_today, p=abs(round(delta)), dir=t("brief.more" if delta >= 0 else "brief.less", lang))
    parts = [head + t("brief.end", lang)]
    if pending:
        p0 = pending[0]
        parts.append(t("brief.pending", lang, name=i18n.name_for(p0, lang), amt=p0["amount_display"]))
    top3 = [i for i in insights if i["severity"] in ("critical", "warning")][:3]
    if top3:
        parts.append(t("brief.count", lang, n=len(top3)))
        for ordinal, i in zip(t("brief.ord", lang).split("|"), top3):
            parts.append(f"{ordinal}: {i['title']}{t('brief.end', lang)}")

    categories = [{"category": c["category"], "label": i18n.CATEGORY[lang].get(c["category"], c["category"]),
                   "share_pct": c["share_pct"], "sales_display": c["sales_display"],
                   "change_pct": c.get("pct_change_vs_previous_period")}
                  for c in sorted(products["categories_weakest_first"], key=lambda c: -c["share_pct"])]
    return {
        "lang": lang,
        "greeting": {"owner": owner, "store": _store_name(data, lang), "date": today.isoformat(),
                     "date_display": i18n.date_display(today, lang)},
        "kpis": kpis, "insights": insights, "briefing": " ".join(parts),
        "sales_28d": sales_28d, "weekday_avg": weekday_avg,
        "weakest_weekday": weakest, "weakest_weekday_hi": i18n.WEEKDAY[lang].get(weakest) if weakest else None,
        "weakest_gap_pct": gap_pct, "categories": categories, "slow_products": products["slowest_products"][:3],
        "khata": khata, "lapsed": lapsed["lapsed_customers"], "pending_payments": pending,
    }
