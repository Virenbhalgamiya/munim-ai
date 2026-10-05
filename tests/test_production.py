"""Production behaviour: auth, isolation, persistence, ledger writes, business rules, API hardening."""
import pytest
from fastapi.testclient import TestClient

from app import auth, config
from app.agent import tools
from app.agent.agent import MunimAgent
from app.db import store
from app.db.database import get_conn, transaction
from app.services.llm_service import parse_blocks
from tests.conftest import DEMO_MERCHANT, DEMO_PHONE, DEMO_PIN, FAKE_OTP


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def login(c, phone=DEMO_PHONE, code=FAKE_OTP):
    """Real login flow: request OTP, then verify it (Message Central is replaced by the test double)."""
    assert c.post("/api/otp/send", json={"phone": phone}).status_code == 200
    return c.post("/api/otp/verify", json={"phone": phone, "code": code})


def ask(c, text, sid="t"):
    return c.post("/api/ask", json={"text": text, "session_id": sid})


def make_second_merchant():
    with transaction() as conn:
        auth.create_merchant("patil-kirana", "Patil Kirana", "Sunita", "9822000002", "482915", "Nashik", conn=conn)
        cust = store.create_customer("patil-kirana", "Ganesh", conn=conn)
        store.add_udhaar_entry("patil-kirana", cust["id"], "credit", 900, "2026-09-30", "", "seed", "seed", conn=conn)


# ------------------------------------------------------------------ auth
def test_pin_is_hashed_and_weak_pins_rejected():
    h = auth.hash_pin("482915")
    assert "482915" not in h and auth.verify_pin("482915", h) and not auth.verify_pin("482916", h)
    for weak in ("1234", "0000", "12", "abcd"):
        with pytest.raises(ValueError):
            auth.validate_pin(weak)
    row = get_conn().execute("SELECT pin_hash FROM merchants WHERE id = ?", (DEMO_MERCHANT,)).fetchone()
    assert row["pin_hash"].startswith("scrypt$") and DEMO_PIN not in row["pin_hash"]


def test_login_logout_and_protected_routes(client):
    assert client.post("/api/ask", json={"text": "hi"}).status_code == 401
    assert client.get("/api/udhaar").status_code == 401
    assert login(client, code="111111").status_code == 401
    r = login(client)
    assert r.status_code == 200 and r.json()["merchant"]["store_name"] == "Sharma General Store"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/me").json()["merchant"]["id"] == DEMO_MERCHANT
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_lockout_after_repeated_wrong_otps(client):
    client.post("/api/otp/send", json={"phone": DEMO_PHONE})
    for _ in range(config.OTP_MAX_ATTEMPTS - 1):
        assert client.post("/api/otp/verify", json={"phone": DEMO_PHONE, "code": "111111"}).status_code == 401
    assert client.post("/api/otp/verify", json={"phone": DEMO_PHONE, "code": "111111"}).status_code == 423
    # the phone is now locked: even a fresh OTP request is refused
    assert client.post("/api/otp/send", json={"phone": DEMO_PHONE}).status_code == 423


def test_expired_session_is_rejected(client):
    login(client)
    with transaction() as conn:
        conn.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert client.get("/api/me").status_code == 401


def test_bearer_token_auth_for_api_clients():
    token, _ = auth.login(DEMO_PHONE, DEMO_PIN)
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert c.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401


# ------------------------------------------------------------------ isolation
def test_merchants_cannot_see_each_others_data(client):
    make_second_merchant()
    with store.merchant_scope("patil-kirana"):
        assert tools.udhaar_lookup("Suresh")["found"] is False
        assert tools.udhaar_lookup()["total_outstanding"] == 900
    login(client, "9822000002")
    names = [c["customer_name"] for c in client.get("/api/udhaar").json()["customers"]]
    assert names == ["Ganesh"]
    r = ask(client, "Suresh ka kitna udhaar baaki hai?")
    assert r.status_code == 200 and "1,850" not in r.json()["reply"]
    # and the demo merchant still sees its own ledger
    client.post("/api/logout")
    login(client)
    assert "Ganesh" not in [c["customer_name"] for c in client.get("/api/udhaar").json()["customers"]]


# ------------------------------------------------------------------ udhaar ledger by voice
def test_record_payment_two_turns_updates_ledger_and_audit(agent):
    r1 = agent.ask("Suresh ne 500 rupaye diye", session_id="u1")
    call = r1.tool_calls[0]
    assert call.name == "record_udhaar" and call.output["status"] == "confirmation_required"
    assert tools.udhaar_lookup("Suresh")["customers"][0]["outstanding"] == 1850  # nothing saved yet
    r2 = agent.ask("Haan, likh do", session_id="u1")
    assert r2.tool_calls[0].output["status"] == "saved"
    assert tools.udhaar_lookup("Suresh")["customers"][0]["outstanding"] == 1350
    actions = [a["action"] for a in store.recent_audit(DEMO_MERCHANT, 5)]
    assert "udhaar.payment" in actions


def test_record_udhaar_validation():
    assert tools.record_udhaar("Suresh", 5000, "payment", confirmed=True)["status"] == "error"   # more than owed
    assert tools.record_udhaar("Nobody", 100, "payment")["status"] == "error"
    assert tools.record_udhaar("Suresh", -5, "payment")["status"] == "error"
    assert tools.record_udhaar("Suresh", 100, "refund")["status"] == "error"
    assert tools.udhaar_lookup("Suresh")["customers"][0]["outstanding"] == 1850


def test_credit_to_new_customer_creates_khata():
    preview = tools.record_udhaar("Ramu", 300, "credit")
    assert preview["status"] == "confirmation_required" and preview["new_customer"]
    saved = tools.record_udhaar("Ramu", 300, "credit", note="atta", confirmed=True)
    assert saved["status"] == "saved"
    assert tools.udhaar_lookup("Ramu")["customers"][0]["outstanding"] == 300


class _ScriptedLLM:
    """Plays back fixed tool calls, one per turn, then a short text."""

    def __init__(self, calls):
        self.calls, self.i = list(calls), 0

    def chat(self, system, messages, tools_):
        last = messages[-1]
        if isinstance(last["content"], list):  # tool results -> finish the turn
            return parse_blocks([{"type": "text", "text": "ok"}], "end_turn")
        call = self.calls[self.i]
        self.i += 1
        if call is None:
            return parse_blocks([{"type": "text", "text": "ok"}], "end_turn")
        return parse_blocks([{"type": "tool_use", "id": f"c{self.i}", "name": call[0], "input": call[1]}], "tool_use")


def test_guard_requires_confirmation_of_the_same_action_in_the_next_turn(telegram):
    pay = {"customer_name": "Suresh", "amount": 500, "kind": "payment"}
    agent = MunimAgent(llm=_ScriptedLLM([
        ("record_udhaar", {**pay, "confirmed": False}),                    # turn 1: preview ₹500
        ("record_udhaar", {**pay, "amount": 900, "confirmed": True}),      # turn 2: confirm a DIFFERENT amount
        ("record_udhaar", {**pay, "confirmed": False}),                    # turn 3: preview ₹500 again
        None,                                                              # turn 4: unrelated
        ("record_udhaar", {**pay, "confirmed": True}),                     # turn 5: stale confirm
    ]))
    agent.ask("Suresh ne 500 diye", session_id="g")
    r2 = agent.ask("haan", session_id="g")
    assert r2.tool_calls[0].guard_note and r2.tool_calls[0].output["status"] == "confirmation_required"
    agent.ask("Suresh ne 500 diye", session_id="g")
    agent.ask("aur kuch?", session_id="g")
    r5 = agent.ask("haan", session_id="g")
    assert r5.tool_calls[0].guard_note
    assert tools.udhaar_lookup("Suresh")["customers"][0]["outstanding"] == 1850


# ------------------------------------------------------------------ reminders: rules + history
def test_reminder_cooldown_and_history(telegram):
    assert tools.send_reminder("Suresh", confirmed=True)["status"] == "sent"
    again = tools.send_reminder("Suresh")
    assert again["status"] == "blocked_cooldown" and len(telegram) == 1
    row = get_conn().execute("SELECT status, via FROM reminders").fetchone()
    assert (row["status"], row["via"]) == ("sent", "telegram")


def test_reminder_without_telegram_setup_says_so(monkeypatch, telegram):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    r = tools.send_reminder("Suresh", confirmed=True)
    assert r["status"] == "not_configured" and r["sent"] is False and telegram == []
    assert config.validate()[1]  # warning raised at startup


def test_failed_telegram_send_is_recorded(monkeypatch):
    class Down:
        def send_message(self, *a, **k):
            raise RuntimeError("Telegram error 400: chat not found")
    monkeypatch.setattr("app.services.telegram_service.get_telegram_service", lambda: Down())
    r = tools.send_reminder("Suresh", confirmed=True)
    assert r["status"] == "error" and "chat not found" in r["message"]
    row = get_conn().execute("SELECT status, error FROM reminders").fetchone()
    assert row["status"] == "failed" and "chat not found" in row["error"]
    assert tools.send_reminder("Suresh")["status"] == "confirmation_required"  # failed sends don't start cooldown


def test_opted_out_customer_is_never_reminded(telegram):
    cid = tools.udhaar_lookup("Suresh")["customers"][0]
    sid = [c for c in store.list_customers(DEMO_MERCHANT) if c["name"] == "Suresh"][0]["id"]
    store.set_opt_out(DEMO_MERCHANT, sid, True)
    assert tools.send_reminder("Suresh", confirmed=True)["status"] == "blocked_opt_out"
    assert telegram == [] and cid["outstanding"] == 1850


def test_reminder_message_uses_merchant_identity():
    make_second_merchant()
    with store.merchant_scope("patil-kirana"):
        msg = tools.send_reminder("Ganesh")["reminder_message"]
    assert "Patil Kirana" in msg and "Sunita" in msg and "Sharma" not in msg


# ------------------------------------------------------------------ persistence
def test_conversation_survives_restart(telegram):
    MunimAgent().ask("Suresh ko payment ka reminder bhej do", session_id="p1")
    r = MunimAgent().ask("Haan, bhej do", session_id="p1")   # brand-new agent = server restart
    assert [t.output["status"] for t in r.tool_calls] == ["sent"]
    assert len(store.conversation_transcript(DEMO_MERCHANT, "p1")) == 4


def test_conversation_endpoint_restores_chat(client):
    login(client)
    ask(client, "Suresh ka kitna udhaar baaki hai?", sid="restore")
    msgs = client.get("/api/conversation", params={"session_id": "restore"}).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "munim"] and "1,850" in msgs[1]["text"]
    client.post("/api/reset", json={"session_id": "restore"})
    assert client.get("/api/conversation", params={"session_id": "restore"}).json()["messages"] == []


# ------------------------------------------------------------------ resilience
class _DownLLM:
    def chat(self, *a, **k):
        raise ConnectionError("no internet")


def test_ai_outage_gives_honest_reply():
    r = MunimAgent(llm=_DownLLM()).ask("Suresh ka kitna udhaar baaki hai?", session_id="o2")
    assert r.error and "माफ़" in r.reply and r.tool_calls == []
    r2 = MunimAgent().ask("Suresh ka kitna udhaar baaki hai?", session_id="o2")  # history still valid afterwards
    assert "1,850" in r2.reply


# ------------------------------------------------------------------ API hardening
def test_security_headers(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-request-id"]
    login(client)
    assert client.get("/api/me").headers["cache-control"] == "no-store"


def test_input_limits(client, monkeypatch):
    login(client)
    assert ask(client, "x" * (config.MAX_TEXT_CHARS + 1)).status_code == 422
    assert client.post("/api/ask", json={"text": "hi", "session_id": "../etc"}).status_code == 422
    assert client.post("/api/ask", json={"session_id": "t"}).status_code == 422
    assert client.post("/api/ask", json={"text": "   ", "session_id": "t"}).status_code == 400


def test_ask_rate_limit(client, monkeypatch):
    monkeypatch.setattr(config, "ASK_RATE_PER_MINUTE", 2)
    login(client)
    assert ask(client, "Aaj ka hisaab?").status_code == 200
    assert ask(client, "Aaj ka hisaab?").status_code == 200
    r = ask(client, "Aaj ka hisaab?")
    assert r.status_code == 429 and "retry-after" in r.headers


def test_health_ready_and_admin_metrics(client):
    assert client.get("/health").json()["ok"] is True
    assert client.get("/ready").json()["checks"] == {"database": True, "ai_configured": True, "telegram_configured": True}
    assert client.get("/api/admin/metrics").status_code == 403
    assert client.get("/api/admin/metrics", headers={"X-Admin-Token": "wrong"}).status_code == 403
    m = client.get("/api/admin/metrics", headers={"X-Admin-Token": "test-admin-token"}).json()
    assert m["counters"]["http_requests"] >= 1


def test_unknown_tool_args_dont_crash():
    assert "error" in tools.execute_tool("record_udhaar", {"bogus": 1})


class _StaleThenToolLLM:
    """First draft copies an old number without a tool; after the nudge it calls the tool."""

    def __init__(self):
        self.systems = []

    def chat(self, system, messages, tools_):
        self.systems.append(system)
        last = messages[-1]
        if isinstance(last["content"], list):
            out = __import__("json").loads(last["content"][0]["content"])
            return parse_blocks([{"type": "text", "text": f"Suresh ji ka {out['customers'][0]['outstanding_display']} baaki hai."}], "end_turn")
        if "IMPORTANT" not in system:
            return parse_blocks([{"type": "text", "text": "Suresh ji ka ₹1,850 baaki hai."}], "end_turn")   # stale
        return parse_blocks([{"type": "tool_use", "id": "t1", "name": "udhaar_lookup", "input": {"customer_name": "Suresh"}}], "tool_use")


def test_numeric_answer_without_tool_is_redone_with_fresh_data():
    tools.record_udhaar("Suresh", 500, "payment", confirmed=True)       # balance is now ₹1,350
    llm = _StaleThenToolLLM()
    r = MunimAgent(llm=llm).ask("Suresh ka kitna udhaar baaki hai?", session_id="stale")
    assert [t.name for t in r.tool_calls] == ["udhaar_lookup"]
    assert "1,350" in r.reply and "1,850" not in r.reply
    assert any("IMPORTANT" in s for s in llm.systems)


def test_plain_chat_without_numbers_is_not_retried():
    class Chat:
        calls = 0

        def chat(self, system, messages, tools_):
            Chat.calls += 1
            return parse_blocks([{"type": "text", "text": "Namaste! Kya madad karun?"}], "end_turn")

    r = MunimAgent(llm=Chat()).ask("namaste", session_id="hi")
    assert r.reply.startswith("Namaste") and Chat.calls == 1


def test_dashboard_insights(client):
    assert client.get("/api/dashboard").status_code == 401
    login(client)
    d = client.get("/api/dashboard").json()
    assert [k["id"] for k in d["kpis"]] == ["sales", "payments", "udhaar", "risk"]
    assert d["kpis"][0]["display"] == "₹13,535" and len(d["kpis"][0]["trend"]) == 14
    ids = [i["id"] for i in d["insights"]]
    assert {"lapsed", "overdue", "weekday_dip", "category_drop", "slow_stock", "peak_hours"} <= set(ids)
    sev = [i["severity"] for i in d["insights"]]
    assert sev == sorted(sev, key=["critical", "warning", "opportunity", "info"].index)     # most urgent first
    assert all(i["impact"] for i in d["insights"])
    assert len(d["sales_28d"]) == 28 and d["sales_28d"][-1]["date"] == "2026-10-02"
    assert d["weakest_weekday"] == "Tuesday" and 25 <= d["weakest_gap_pct"] <= 40
    assert d["briefing"].startswith("नमस्ते रमेश जी") and "₹13,535" in d["briefing"]


def test_dashboard_reflects_new_ledger_entries_and_is_per_merchant(client):
    login(client)
    tools.record_udhaar("Suresh", 1850, "payment", confirmed=True)       # Suresh pays in full
    d = client.get("/api/dashboard").json()
    assert "overdue" not in [i["id"] for i in d["insights"]]
    assert "Suresh" not in [c["customer_name"] for c in d["khata"]["customers"]]
    make_second_merchant()
    client.post("/api/logout")
    login(client, "9822000002")
    d2 = client.get("/api/dashboard").json()
    assert d2["greeting"]["store"] == "Patil Kirana" and d2["khata"]["total_outstanding"] == 900
