"""Stress / concurrency tests against the real server process (real HTTP, real SQLite, many threads).

Prints a latency + correctness report. AI = deterministic test brain (no Groq quota used).
"""
from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from tests_e2e.test_functional import Server

REPORT: list[str] = []


@pytest.fixture(scope="module")
def fast_server():
    s = Server(ask_rate="100000").start()      # rate limit lifted to measure raw capacity
    yield s
    s.stop()
    print("\n\n===== STRESS REPORT =====\n" + "\n".join(REPORT) + "\n=========================")


def session(server) -> requests.Session:
    s = requests.Session()
    assert s.post(server.url + "/api/dev/login", timeout=10).status_code == 200
    return s


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))] * 1000


def test_parallel_conversations(fast_server):
    """20 users × 5 questions at the same time."""
    questions = ["आज का हिसाब कैसा रहा?", "किसका कितना उधार बाकी है?", "कौन से पुराने ग्राहक आना बंद हो गए?",
                 "क्या चीज़ नहीं बिक रही है?", "मंगलवार को धंधा कैसा गया?"]
    lat, codes, errors = [], [], []

    def user(i):
        s = session(fast_server)
        for q in questions:
            t = time.perf_counter()
            r = s.post(fast_server.url + "/api/ask", json={"text": q, "session_id": f"stress-{i}"}, timeout=60)
            lat.append(time.perf_counter() - t)
            codes.append(r.status_code)
            if r.status_code == 200 and (r.json().get("error") or not r.json()["tool_calls"]):
                errors.append(r.json()["reply"])

    with ThreadPoolExecutor(20) as ex:
        list(ex.map(user, range(20)))
    REPORT.append(f"parallel asks: {len(codes)} requests, 20 users | p50 {pct(lat, .5):.0f} ms, p95 {pct(lat, .95):.0f} ms, "
                  f"max {max(lat) * 1000:.0f} ms | non-200: {sum(c != 200 for c in codes)} | failed answers: {len(errors)}")
    assert set(codes) == {200} and not errors
    # every conversation was stored in full
    assert fast_server.sql("SELECT COUNT(DISTINCT session_id) FROM agent_sessions WHERE session_id LIKE 'stress-%'")[0][0] == 20
    assert fast_server.sql("SELECT MAX(turn) FROM agent_sessions WHERE session_id LIKE 'stress-%'")[0][0] == 5


def test_concurrent_khata_writes_lose_nothing(fast_server):
    """25 people each add ₹10 udhaar for the same customer at the same moment."""
    before = fast_server.sql("SELECT COALESCE(SUM(CASE kind WHEN 'credit' THEN amount ELSE -amount END),0) "
                             "FROM udhaar_entries e JOIN customers c ON c.id = e.customer_id WHERE c.name = 'Deepak Mehta'")[0][0]
    barrier = threading.Barrier(25)
    results = []

    def writer(i):
        s = session(fast_server)
        sid = f"write-{i}"
        s.post(fast_server.url + "/api/ask", json={"text": "Deepak ko 10 rupaye ka saman udhaar diya", "session_id": sid}, timeout=60)
        barrier.wait()                                    # all confirmations fire together
        r = s.post(fast_server.url + "/api/ask", json={"text": "haan likh do", "session_id": sid}, timeout=60)
        results.append([t["output"].get("status") for t in r.json()["tool_calls"]])

    with ThreadPoolExecutor(25) as ex:
        list(ex.map(writer, range(25)))
    after = fast_server.sql("SELECT SUM(CASE kind WHEN 'credit' THEN amount ELSE -amount END) "
                            "FROM udhaar_entries e JOIN customers c ON c.id = e.customer_id WHERE c.name = 'Deepak Mehta'")[0][0]
    saved = sum("saved" in r for r in results)
    audit = fast_server.sql("SELECT COUNT(*) FROM audit_log WHERE action = 'udhaar.credit' AND actor = 'merchant'")[0][0]
    REPORT.append(f"concurrent khata writes: 25 simultaneous confirmations | saved {saved} | balance {before} -> {after} "
                  f"(expected +{25 * 10}) | audit rows {audit}")
    assert saved == 25 and after - before == 250


def test_concurrent_payments_cannot_overdraw(fast_server):
    """30 simultaneous ₹100 payments against Meena's ₹640: at most 6 may succeed, balance never negative."""
    barrier = threading.Barrier(30)
    statuses = []

    def payer(i):
        s = session(fast_server)
        sid = f"pay-{i}"
        s.post(fast_server.url + "/api/ask", json={"text": "Meena ne 100 rupaye diye", "session_id": sid}, timeout=60)
        barrier.wait()
        r = s.post(fast_server.url + "/api/ask", json={"text": "haan likh do", "session_id": sid}, timeout=60)
        statuses.extend(t["output"].get("status") for t in r.json()["tool_calls"])

    with ThreadPoolExecutor(30) as ex:
        list(ex.map(payer, range(30)))
    balance = fast_server.sql("SELECT SUM(CASE kind WHEN 'credit' THEN amount ELSE -amount END) "
                              "FROM udhaar_entries e JOIN customers c ON c.id = e.customer_id WHERE c.name = 'Meena Patil'")[0][0]
    REPORT.append(f"overdraw race: 30 simultaneous ₹100 payments on ₹640 | saved {statuses.count('saved')} | "
                  f"rejected {statuses.count('error')} | final balance ₹{balance}")
    assert statuses.count("saved") <= 6 and balance >= 0 and balance == 640 - 100 * statuses.count("saved")


def test_dashboard_under_load(fast_server):
    s = session(fast_server)
    cookies = s.cookies.get_dict()
    lat, codes = [], []

    def hit(_):
        t = time.perf_counter()
        r = requests.get(fast_server.url + "/api/dashboard", cookies=cookies, timeout=30)
        lat.append(time.perf_counter() - t)
        codes.append(r.status_code)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(30) as ex:
        list(ex.map(hit, range(300)))
    total = time.perf_counter() - t0
    REPORT.append(f"dashboard: 300 loads, 30 parallel | {300 / total:.0f} req/s | p50 {pct(lat, .5):.0f} ms, "
                  f"p95 {pct(lat, .95):.0f} ms | non-200: {sum(c != 200 for c in codes)}")
    assert set(codes) == {200} and pct(lat, .95) < 3000


def test_parallel_neural_voice(fast_server):
    s = session(fast_server)
    cookies = s.cookies.get_dict()
    texts = [f"सुरेश जी का ₹{1000 + i} उधार बाकी है।" for i in range(8)]
    out = []

    def tts(text):
        t = time.perf_counter()
        r = requests.post(fast_server.url + "/api/tts", json={"text": text}, cookies=cookies, timeout=60)
        out.append((r.status_code, len(r.content), time.perf_counter() - t))

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(tts, texts))
    ok = [o for o in out if o[0] == 200 and o[1] > 5000]
    REPORT.append(f"neural voice: 8 parallel | ok {len(ok)}/8 | avg {statistics.mean(o[2] for o in out) * 1000:.0f} ms")
    assert len(ok) == 8


def test_rate_limit_is_exact_under_parallel_load():
    s = Server(ask_rate="10").start()
    try:
        sess = session(s)
        cookies = sess.cookies.get_dict()
        codes = []

        def hit(i):
            codes.append(requests.post(s.url + "/api/ask", json={"text": "आज का हिसाब?", "session_id": f"rl-{i}"},
                                       cookies=cookies, timeout=60).status_code)

        with ThreadPoolExecutor(25) as ex:
            list(ex.map(hit, range(25)))
        REPORT.append(f"rate limit 10/min: 25 parallel asks | allowed {codes.count(200)} | refused 429: {codes.count(429)}")
        assert codes.count(200) == 10 and codes.count(429) == 15
    finally:
        s.stop()


def test_server_healthy_after_stress(fast_server):
    r = requests.get(fast_server.url + "/ready", timeout=10).json()
    m = requests.get(fast_server.url + "/api/admin/metrics", headers={"X-Admin-Token": "e2e-admin"}, timeout=10).json()
    REPORT.append(f"after stress: ready={r['ok']} | http_5xx={m['counters'].get('http_5xx', 0)} | "
                  f"agent_errors={m['counters'].get('agent_errors', 0)} | requests={m['counters'].get('http_requests')}")
    assert r["ok"] and m["counters"].get("http_5xx", 0) == 0 and m["counters"].get("agent_errors", 0) == 0


def test_simultaneous_reminder_confirmations_send_once(fast_server):
    """10 sessions preview a reminder for Suresh, then all say "haan" at the same instant: one message only."""
    barrier = threading.Barrier(10)
    statuses = []

    def confirmer(i):
        s = session(fast_server)
        sid = f"remind-{i}"
        s.post(fast_server.url + "/api/ask", json={"text": "Suresh ko payment ka reminder bhej do", "session_id": sid}, timeout=60)
        barrier.wait()
        r = s.post(fast_server.url + "/api/ask", json={"text": "haan bhej do", "session_id": sid}, timeout=60)
        statuses.extend(t["output"].get("status") for t in r.json()["tool_calls"])

    before = len(fast_server.telegram())
    with ThreadPoolExecutor(10) as ex:
        list(ex.map(confirmer, range(10)))
    sent = len(fast_server.telegram()) - before
    REPORT.append(f"double-send race: 10 simultaneous reminder confirmations | Telegram messages {sent} | "
                  f"blocked {statuses.count('blocked_cooldown')}")
    assert sent == 1 and statuses.count("sent") == 1


def test_parallel_payment_decisions_and_webhook_storm():
    """20 simultaneous 'हाँ' clicks on one payment + the same webhook delivered 20 times at once."""
    s = Server(ask_rate="100000", webhook_secret="storm").start()
    try:
        sess = session(s)
        cookies = sess.cookies.get_dict()
        pid = sess.get(s.url + "/api/payments/pending", timeout=10).json()["payments"][0]["id"]
        barrier = threading.Barrier(20)
        codes = []

        def click(_):
            barrier.wait()
            codes.append(requests.post(f"{s.url}/api/payments/{pid}/link", cookies=cookies, timeout=30).status_code)

        with ThreadPoolExecutor(20) as ex:
            list(ex.map(click, range(20)))
        upi_entries = s.sql("SELECT COUNT(*) FROM udhaar_entries WHERE source = 'upi'")[0][0]

        barrier2 = threading.Barrier(20)
        hook = []

        def deliver(_):
            barrier2.wait()
            hook.append(requests.post(s.url + "/api/webhooks/upi", json={"merchant_id": "sharma-gs", "txn_id": "STORM-1",
                                                                            "amount": 250, "payer_vpa": "suresh@paytm"},
                                      headers={"X-Webhook-Secret": "storm"}, timeout=30).status_code)

        with ThreadPoolExecutor(20) as ex:
            list(ex.map(deliver, range(20)))
        stored = s.sql("SELECT COUNT(*) FROM incoming_payments WHERE provider_txn_id = 'STORM-1'")[0][0]
        REPORT.append(f"payment decisions: 20 simultaneous 'हाँ' clicks | applied {codes.count(200)} | refused 409: {codes.count(409)} "
                      f"| khata entries {upi_entries} || webhook storm: 20 duplicate deliveries | stored {stored} | "
                      f"5xx {sum(c >= 500 for c in hook)}")
        assert codes.count(200) == 1 and upi_entries == 1 and stored == 1 and all(c < 500 for c in hook)
    finally:
        s.stop()
