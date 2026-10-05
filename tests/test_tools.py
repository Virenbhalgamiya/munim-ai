from collections import defaultdict
from datetime import date

import pytest

from app.agent import tools
from app.db import store


@pytest.fixture
def data():
    return store.load_merchant_data("sharma-gs")


# ------------------------------------------------------------------ dataset patterns
def test_dataset_shape(data):
    assert data["store"]["store_name"] == "Sharma General Store"
    assert data["period"]["days"] == 60
    assert data["period"]["end"] == "2026-10-02"
    per_day = defaultdict(int)
    for t in data["transactions"]:
        assert 20 <= t["amount"] <= 800
        per_day[t["date"]] += 1
    assert len(per_day) == 60
    assert all(30 <= n <= 80 for n in per_day.values())


def test_every_tuesday_is_25_to_35_pct_below_week_average(data):
    totals = defaultdict(int)
    for t in data["transactions"]:
        totals[date.fromisoformat(t["date"])] += t["amount"]
    weeks = defaultdict(list)
    for d, v in totals.items():
        weeks[d.isocalendar()[:2]].append((d, v))
    tuesdays = 0
    for days in weeks.values():
        avg = sum(v for _, v in days) / len(days)
        for d, v in days:
            if d.weekday() == 1:
                tuesdays += 1
                assert 0.25 <= 1 - v / avg <= 0.35, d
    assert tuesdays >= 8


# ------------------------------------------------------------------ tools
def test_fmt_inr():
    assert tools.fmt_inr(1850) == "₹1,850"
    assert tools.fmt_inr(123456) == "₹1,23,456"
    assert tools.fmt_inr(999) == "₹999"


@pytest.mark.parametrize("value,expected", [
    (None, "2026-10-02"), ("aaj", "2026-10-02"), ("kal", "2026-10-01"),
    ("mangalvaar", "2026-09-29"), ("Tuesday", "2026-09-29"), ("मंगलवार", "2026-09-29"),
    ("2026-09-15", "2026-09-15"),
])
def test_resolve_date(value, expected):
    assert tools.resolve_date(value).isoformat() == expected


def test_daily_summary_tuesday_surfaces_dip():
    r = tools.daily_summary("tuesday")
    assert r["weekday"] == "Tuesday"
    assert r["weakest_weekday"] == "Tuesday"
    assert 25 <= r["weakest_weekday_pct_below_other_days"] <= 40
    assert r["pct_vs_overall_daily_avg"] < -20
    assert r["insights"][0]["type"] == "weekday_dip"


def test_daily_summary_no_data():
    assert "error" in tools.daily_summary("2020-01-01")


@pytest.mark.parametrize("name", ["Suresh", "suresh bhai", "Suresh ji", "सुरेश"])
def test_udhaar_suresh(name):
    r = tools.udhaar_lookup(name)
    assert r["found"]
    u = r["customers"][0]
    assert u["customer_name"] == "Suresh"
    assert u["outstanding"] == 1850
    assert u["is_overdue"] and u["days_since_last_payment"] > 10


def test_udhaar_list_sorted_overdue_first():
    r = tools.udhaar_lookup()
    assert 5 <= r["customer_count"] <= 6
    assert r["customers"][0]["customer_name"] == "Suresh"
    assert all(200 <= c["outstanding"] <= 3000 for c in r["customers"])


def test_udhaar_unknown_customer():
    assert tools.udhaar_lookup("Bholu")["found"] is False


def test_lapsed_customers():
    r = tools.lapsed_customers(10)
    names = {c["customer_name"] for c in r["lapsed_customers"]}
    assert names == {"Anita Desai", "Vikram Joshi", "Kavita Shinde"}
    assert all(c["historical_visits_per_week"] >= 2 for c in r["lapsed_customers"])
    assert r["active_regulars_count"] == 7


def test_product_performance_household_weakest():
    r = tools.product_performance(14)
    assert r["categories_weakest_first"][0]["category"] == "household"
    assert r["slowest_products"][0]["category"] == "household"


def test_send_reminder_defaults_to_preview(telegram):
    r = tools.send_reminder("Suresh")
    assert r["status"] == "confirmation_required" and r["sent"] is False
    assert "1,850" in r["reminder_message"]
    assert telegram == []


def test_send_reminder_confirmed_sends_telegram(telegram):
    r = tools.send_reminder("Suresh", confirmed=True)
    assert r["status"] == "sent"
    assert len(telegram) == 1 and r["reminder_message"] in telegram[0]["text"] and telegram[0]["chat_id"] == "1000001"


def test_execute_tool_handles_errors():
    assert "error" in tools.execute_tool("nope", {})
    assert "error" in tools.execute_tool("daily_summary", {"date": "banana"})
