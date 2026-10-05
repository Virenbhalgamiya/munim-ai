"""UPI repayment matching.

A UPI payment that is not a counter sale (no bill) arrives, e.g. a customer sends money from home.
If the payer owes udhaar, Munim does NOT change the khata on its own: it asks the merchant
"repayment or purchase?". Only on "yes" is the payment written to the udhaar ledger.

Where payments come from:
  * production: Paytm payment notification -> POST /api/webhooks/upi (shared-secret header)
  * demo:       developer-mode "simulate UPI payment" button, same code path (source='demo')
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from app import config
from app.agent.tools import fmt_inr, match_customer
from app.db import store
from app.db.database import get_conn, now_iso, transaction

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    pass


def _match_payer(merchant_id: str, payer_vpa: str, payer_name: str) -> dict | None:
    customers = store.list_customers(merchant_id)
    vpa = (payer_vpa or "").strip().lower()
    if vpa:
        by_vpa = [c for c in customers if c.get("upi_vpa") and c["upi_vpa"].lower() == vpa]
        if len(by_vpa) == 1:
            return by_vpa[0]
    if payer_name:
        by_name = match_customer(payer_name, customers, key="name")
        if len(by_name) == 1:
            return by_name[0]
    return None


def _view(row) -> dict:
    d = dict(row)
    cust = store.get_customer(d["merchant_id"], d["customer_id"]) if d.get("customer_id") else None
    outstanding = store.balance_of(d["merchant_id"], d["customer_id"]) if cust else 0
    d.update({
        "customer_name": cust["name"] if cust else d["payer_name"],
        "customer_name_hi": (cust.get("name_hi") or cust["name"]) if cust else d["payer_name"],
        "amount_display": fmt_inr(d["amount"]),
        "outstanding": outstanding, "outstanding_display": fmt_inr(outstanding),
        "would_link": min(d["amount"], outstanding),
        "time": d["received_at"][11:16],
    })
    return d


def record_incoming(merchant_id: str, amount: int, payer_vpa: str = "", payer_name: str = "",
                    provider_txn_id: str | None = None, source: str = "webhook", notify: bool = True) -> dict:
    """Store a non-sale UPI payment. Idempotent on provider_txn_id (webhooks can be delivered twice)."""
    if not isinstance(amount, int) or amount <= 0 or amount > 10_00_000:
        raise PaymentError("invalid amount")
    txn = (provider_txn_id or f"{source}-{uuid.uuid4().hex[:16]}")[:80]
    existing = get_conn().execute("SELECT * FROM incoming_payments WHERE merchant_id = ? AND provider_txn_id = ?",
                                  (merchant_id, txn)).fetchone()
    if existing:
        return {**_view(existing), "duplicate": True}
    customer = _match_payer(merchant_id, payer_vpa, payer_name)
    owes = store.balance_of(merchant_id, customer["id"]) if customer else 0
    status = "pending" if owes > 0 else "unmatched"
    received = f"{store.today_for(merchant_id).isoformat()}T{datetime.now(timezone.utc).astimezone().strftime('%H:%M:%S')}"
    try:
        row = _insert_payment(merchant_id, txn, payer_vpa, payer_name, customer, amount, received, status, source)
    except sqlite3.IntegrityError:          # the same webhook delivered twice at the same instant
        existing = get_conn().execute("SELECT * FROM incoming_payments WHERE merchant_id = ? AND provider_txn_id = ?",
                                      (merchant_id, txn)).fetchone()
        return {**_view(existing), "duplicate": True}
    view = _view(row)
    if status == "pending" and notify:
        _notify(merchant_id, view)
    return view


def _insert_payment(merchant_id, txn, payer_vpa, payer_name, customer, amount, received, status, source):
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO incoming_payments (merchant_id, provider_txn_id, payer_vpa, payer_name, customer_id, amount, "
            "received_at, status, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (merchant_id, txn, payer_vpa[:120], payer_name[:120], customer["id"] if customer else None, amount,
             received, status, source))
        store.audit(merchant_id, "upi", "payment.received", {"payment_id": cur.lastrowid, "amount": amount,
                                                              "customer_id": customer["id"] if customer else None,
                                                              "status": status, "source": source}, conn)
        return conn.execute("SELECT * FROM incoming_payments WHERE id = ?", (cur.lastrowid,)).fetchone()


def _notify(merchant_id: str, p: dict) -> None:
    """Best effort: tell the merchant on Telegram that a possible udhaar repayment arrived."""
    if not config.telegram_configured():
        return
    try:
        from app.services.telegram_service import get_telegram_service
        m = store.get_merchant(merchant_id)
        get_telegram_service().send_message(
            f"💰 {p['customer_name_hi']} ने {p['amount_display']} UPI से भेजे हैं।\n"
            f"उनका {p['outstanding_display']} उधार बाकी है। क्या यह उधार की वापसी है?\n"
            f"मुनीम ऐप में \"हाँ, उधार में जमा करें\" दबाइए।", m.get("telegram_chat_id"))
    except Exception as e:  # never block the payment on a notification
        logger.warning("repayment notification failed: %s", e)


def list_pending(merchant_id: str) -> list[dict]:
    rows = get_conn().execute("SELECT * FROM incoming_payments WHERE merchant_id = ? AND status = 'pending' "
                              "ORDER BY id DESC", (merchant_id,)).fetchall()
    return [_view(r) for r in rows]


def get_payment(merchant_id: str, payment_id: int) -> dict | None:
    row = get_conn().execute("SELECT * FROM incoming_payments WHERE merchant_id = ? AND id = ?",
                             (merchant_id, payment_id)).fetchone()
    return _view(row) if row else None


def link_as_repayment(merchant_id: str, payment_id: int, actor: str = "merchant") -> dict:
    """Merchant said yes: write the repayment to the khata. Atomic, so a double click can't apply it twice.
    If the payment is bigger than the debt, only the debt is cleared and the rest is recorded as a sale."""
    with transaction() as conn:
        row = conn.execute("SELECT * FROM incoming_payments WHERE merchant_id = ? AND id = ?",
                           (merchant_id, payment_id)).fetchone()
        if row is None:
            raise PaymentError("payment not found")
        if row["status"] != "pending":
            raise PaymentError(f"already {row['status']}")
        owes = store.balance_of(merchant_id, row["customer_id"], conn)
        linked = min(row["amount"], owes)
        entry_id = None
        if linked > 0:
            entry_id = store.add_udhaar_entry(merchant_id, row["customer_id"], "payment", linked,
                                              row["received_at"][:10], f"UPI {row['provider_txn_id']}", "upi", actor, conn=conn)
        extra = row["amount"] - linked
        if extra > 0:
            _insert_sale(conn, merchant_id, row, extra)
        conn.execute("UPDATE incoming_payments SET status = 'linked', linked_amount = ?, udhaar_entry_id = ?, decided_at = ? "
                     "WHERE id = ?", (linked, entry_id, now_iso(), payment_id))
        store.audit(merchant_id, actor, "payment.linked", {"payment_id": payment_id, "linked": linked, "as_sale": extra}, conn)
    if extra > 0:
        store.invalidate_cache(merchant_id)
    out = get_payment(merchant_id, payment_id)
    return {**out, "linked_amount": linked, "linked_display": fmt_inr(linked), "extra_as_sale": extra,
            "outstanding_after": owes - linked, "outstanding_after_display": fmt_inr(owes - linked)}


def dismiss_as_purchase(merchant_id: str, payment_id: int, actor: str = "merchant") -> dict:
    """Merchant said no: it was a purchase. It becomes a sale (so sales totals stay right), khata untouched."""
    with transaction() as conn:
        row = conn.execute("SELECT * FROM incoming_payments WHERE merchant_id = ? AND id = ?",
                           (merchant_id, payment_id)).fetchone()
        if row is None:
            raise PaymentError("payment not found")
        if row["status"] != "pending":
            raise PaymentError(f"already {row['status']}")
        _insert_sale(conn, merchant_id, row, row["amount"])
        conn.execute("UPDATE incoming_payments SET status = 'dismissed', decided_at = ? WHERE id = ?", (now_iso(), payment_id))
        store.audit(merchant_id, actor, "payment.dismissed", {"payment_id": payment_id, "amount": row["amount"]}, conn)
    store.invalidate_cache(merchant_id)
    return get_payment(merchant_id, payment_id)


def _insert_sale(conn, merchant_id: str, row, amount: int) -> None:
    conn.execute(
        "INSERT INTO transactions (id, merchant_id, txn_date, txn_time, customer_id, customer_type, payer_vpa, payment_mode, "
        "amount, primary_category) VALUES (?, ?, ?, ?, ?, ?, ?, 'UPI', ?, NULL)",
        (f"{merchant_id}:upi:{row['id']}", merchant_id, row["received_at"][:10], row["received_at"][11:16],
         row["customer_id"], "regular" if row["customer_id"] else "walk-in", row["payer_vpa"], amount))


def demo_vpa(name: str) -> str:
    return f"{(name or 'x').split()[0].lower()}@paytm"

