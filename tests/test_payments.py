"""UPI repayment matching: rules, safety, API, voice tool, merchant isolation."""
import threading

import pytest
from fastapi.testclient import TestClient

from app import config, payments
from app.agent import tools
from app.agent.agent import MunimAgent
from app.db import store
from app.db.database import get_conn, transaction
from app.services.llm_service import parse_blocks
from tests.conftest import DEMO_MERCHANT, DEMO_PHONE, FAKE_OTP

M = DEMO_MERCHANT


def balance(name):
    c = [x for x in store.list_customers(M) if x["name"] == name][0]
    return store.balance_of(M, c["id"])


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def login(c):
    c.post("/api/otp/send", json={"phone": DEMO_PHONE})
    assert c.post("/api/otp/verify", json={"phone": DEMO_PHONE, "code": FAKE_OTP}).status_code == 200


def test_demo_store_has_one_pending_repayment():
    p = payments.list_pending(M)
    assert len(p) == 1 and p[0]["customer_name"] == "Rajesh Gupta" and p[0]["amount"] == 1000
    assert p[0]["customer_name_hi"] == "राजेश गुप्ता" and p[0]["outstanding"] == 2400


def test_payment_from_debtor_is_only_a_suggestion():
    p = payments.record_incoming(M, 500, "suresh@paytm", "", "t1", notify=False)
    assert p["status"] == "pending" and balance("Suresh") == 1850          # khata untouched until the merchant decides


def test_link_reduces_khata_and_is_audited():
    p = payments.record_incoming(M, 500, "suresh@paytm", "", "t2", notify=False)
    out = payments.link_as_repayment(M, p["id"])
    assert out["status"] == "linked" and out["linked_amount"] == 500 and balance("Suresh") == 1350
    entry = get_conn().execute("SELECT kind, amount, source FROM udhaar_entries WHERE id = ?", (out["udhaar_entry_id"],)).fetchone()
    assert tuple(entry) == ("payment", 500, "upi")
    assert "payment.linked" in [a["action"] for a in store.recent_audit(M, 5)]


def test_overpayment_clears_debt_and_rest_is_a_sale():
    sales_before = get_conn().execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE merchant_id = ?", (M,)).fetchone()[0]
    p = payments.record_incoming(M, 1000, "meena@paytm", "", "t3", notify=False)
    out = payments.link_as_repayment(M, p["id"])
    assert out["linked_amount"] == 640 and out["extra_as_sale"] == 360 and balance("Meena Patil") == 0
    sales_after = get_conn().execute("SELECT SUM(amount) FROM transactions WHERE merchant_id = ?", (M,)).fetchone()[0]
    assert sales_after - sales_before == 360


def test_dismiss_counts_as_sale_and_keeps_khata():
    p = payments.record_incoming(M, 150, "suresh@paytm", "", "t4", notify=False)
    payments.dismiss_as_purchase(M, p["id"])
    assert balance("Suresh") == 1850
    assert get_conn().execute("SELECT amount FROM transactions WHERE id = ?", (f"{M}:upi:{p['id']}",)).fetchone()[0] == 150


def test_cannot_decide_twice_even_concurrently():
    p = payments.record_incoming(M, 300, "suresh@paytm", "", "t5", notify=False)
    results = []

    def click():
        try:
            payments.link_as_repayment(M, p["id"])
            results.append("ok")
        except payments.PaymentError:
            results.append("refused")

    threads = [threading.Thread(target=click) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and balance("Suresh") == 1550
    with pytest.raises(payments.PaymentError):
        payments.dismiss_as_purchase(M, p["id"])


def test_unknown_or_debt_free_payers_are_not_suggestions():
    assert payments.record_incoming(M, 200, "stranger@ybl", "Ajnabi", "t6", notify=False)["status"] == "unmatched"
    assert payments.record_incoming(M, 200, "amit@paytm", "", "t7", notify=False)["status"] == "unmatched"   # owes nothing


def test_match_by_name_when_vpa_unknown():
    p = payments.record_incoming(M, 100, "new-id@okaxis", "Deepak Mehta", "t8", notify=False)
    assert p["status"] == "pending" and p["customer_name"] == "Deepak Mehta"


def test_duplicate_webhook_is_ignored():
    a = payments.record_incoming(M, 100, "suresh@paytm", "", "dup-1", notify=False)
    b = payments.record_incoming(M, 100, "suresh@paytm", "", "dup-1", notify=False)
    assert b["duplicate"] and a["id"] == b["id"] and len(payments.list_pending(M)) == 2


def test_merchant_notified_on_telegram(telegram):
    payments.record_incoming(M, 500, "suresh@paytm", "", "t9")
    assert len(telegram) == 1 and "सुरेश" in telegram[0]["text"] and "₹500" in telegram[0]["text"]


def test_invalid_amounts_rejected():
    for bad in (0, -5, 10_00_001):
        with pytest.raises(payments.PaymentError):
            payments.record_incoming(M, bad, "suresh@paytm", "", f"bad{bad}", notify=False)


def test_other_merchant_cannot_touch_payment():
    from tests.test_production import make_second_merchant
    make_second_merchant()
    p = payments.list_pending(M)[0]
    with pytest.raises(payments.PaymentError):
        payments.link_as_repayment("patil-kirana", p["id"])
    assert payments.list_pending("patil-kirana") == []


# ------------------------------------------------------------------ API
def test_api_flow_and_dashboard(client):
    assert client.get("/api/payments/pending").status_code == 401
    login(client)
    d = client.get("/api/dashboard").json()
    assert len(d["pending_payments"]) == 1 and "राजेश गुप्ता ने ₹1,000 UPI से भेजे हैं" in d["briefing"]
    pid = d["pending_payments"][0]["id"]
    r = client.post(f"/api/payments/{pid}/link")
    assert r.status_code == 200 and r.json()["payment"]["outstanding_after_display"] == "₹1,400"
    assert client.post(f"/api/payments/{pid}/link").status_code == 409
    d2 = client.get("/api/dashboard").json()
    assert d2["pending_payments"] == [] and "UPI" not in d2["briefing"]
    raj = [c for c in d2["khata"]["customers"] if c["customer_name"] == "Rajesh Gupta"][0]
    assert raj["outstanding"] == 1400 and raj["days_since_last_payment"] == 0


def test_webhook_requires_secret_and_is_idempotent(client, monkeypatch):
    body = {"merchant_id": M, "txn_id": "PAYTM123", "amount": 700, "payer_vpa": "suresh@paytm"}
    assert client.post("/api/webhooks/upi", json=body).status_code == 404            # disabled without a secret
    monkeypatch.setattr(config, "UPI_WEBHOOK_SECRET", "s3cret")
    assert client.post("/api/webhooks/upi", json=body).status_code == 403
    assert client.post("/api/webhooks/upi", json=body, headers={"X-Webhook-Secret": "nope"}).status_code == 403
    r1 = client.post("/api/webhooks/upi", json=body, headers={"X-Webhook-Secret": "s3cret"}).json()
    r2 = client.post("/api/webhooks/upi", json=body, headers={"X-Webhook-Secret": "s3cret"}).json()
    assert r1["status"] == "pending" and r2["duplicate"] and r1["payment_id"] == r2["payment_id"]


def test_simulate_only_in_dev_mode(client, monkeypatch):
    login(client)
    assert client.post("/api/dev/simulate-upi", json={"customer": "Suresh", "amount": 500}).status_code == 404
    monkeypatch.setattr(config, "DEV_MODE", True)
    r = client.post("/api/dev/simulate-upi", json={"customer": "Suresh", "amount": 500}).json()
    assert r["payment"]["status"] == "pending" and r["payment"]["source"] == "demo"


# ------------------------------------------------------------------ voice tool (two-turn confirmation)
class _Scripted:
    def __init__(self, calls):
        self.calls, self.i = list(calls), 0

    def chat(self, system, messages, tools_):
        if isinstance(messages[-1]["content"], list):
            return parse_blocks([{"type": "text", "text": "ठीक है"}], "end_turn")
        name, args = self.calls[self.i]
        self.i += 1
        return parse_blocks([{"type": "tool_use", "id": f"c{self.i}", "name": name, "input": args}], "tool_use")


def test_voice_link_needs_confirmation_next_turn():
    agent = MunimAgent(llm=_Scripted([
        ("link_upi_payment", {"customer_name": "Rajesh", "confirmed": True}),     # same-turn confirm -> blocked
        ("link_upi_payment", {"customer_name": "Rajesh", "confirmed": False}),    # preview
        ("link_upi_payment", {"customer_name": "Rajesh", "confirmed": True}),     # confirm next turn -> saved
    ]))
    r1 = agent.ask("राजेश के पैसे उधार में जमा कर दो", session_id="v")
    assert r1.tool_calls[0].guard_note and balance("Rajesh Gupta") == 2400
    agent.ask("राजेश के पैसे उधार में जमा कर दो", session_id="v2")
    r3 = agent.ask("हाँ", session_id="v2")
    assert r3.tool_calls[0].output["status"] == "saved" and balance("Rajesh Gupta") == 1400


def test_pending_tool_lists_payments():
    out = tools.pending_upi_payments()
    assert out["count"] == 1 and out["payments"][0]["customer_name_hi"] == "राजेश गुप्ता"
