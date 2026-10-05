"""Merchant-scoped data access. Every read/write goes through here and is filtered by merchant_id.

The active merchant travels in a ContextVar (set by the API from the login session, or by the CLI),
so tools never take a merchant_id argument the LLM could tamper with.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone

from app.db.database import get_conn, now_iso, transaction

current_merchant: ContextVar[str | None] = ContextVar("current_merchant", default=None)

_txn_cache: dict[tuple[str, str], dict] = {}   # (db_path, merchant_id) -> {transactions, period}
_cache_lock = threading.Lock()


class MerchantContextError(RuntimeError):
    pass


@contextmanager
def merchant_scope(merchant_id: str) -> Iterator[str]:
    token = current_merchant.set(merchant_id)
    try:
        yield merchant_id
    finally:
        current_merchant.reset(token)


def require_merchant() -> str:
    mid = current_merchant.get()
    if not mid:
        raise MerchantContextError("No merchant in context")
    return mid


def invalidate_cache(merchant_id: str | None = None) -> None:
    with _cache_lock:
        for key in list(_txn_cache):
            if merchant_id is None or key[1] == merchant_id:
                _txn_cache.pop(key, None)


# ------------------------------------------------------------------ merchants
def get_merchant(merchant_id: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
    return dict(row) if row else None


def get_merchant_by_phone(phone: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM merchants WHERE phone = ?", (phone,)).fetchone()
    return dict(row) if row else None


def public_profile(m: dict) -> dict:
    out = {k: m[k] for k in ("id", "store_name", "owner_name", "location", "phone")}
    out["store_name_hi"] = m.get("store_name_hi") or m["store_name"]
    out["owner_name_hi"] = m.get("owner_name_hi") or m["owner_name"]
    return out


# ------------------------------------------------------------------ customers
def _customer_dict(row) -> dict:
    d = dict(row)
    d["aliases"] = json.loads(d.get("aliases") or "[]")
    return d


def list_customers(merchant_id: str) -> list[dict]:
    rows = get_conn().execute("SELECT * FROM customers WHERE merchant_id = ? ORDER BY name", (merchant_id,)).fetchall()
    return [_customer_dict(r) for r in rows]


def get_customer(merchant_id: str, customer_id: int) -> dict | None:
    row = get_conn().execute("SELECT * FROM customers WHERE merchant_id = ? AND id = ?",
                             (merchant_id, customer_id)).fetchone()
    return _customer_dict(row) if row else None


def create_customer(merchant_id: str, name: str, conn=None) -> dict:
    conn = conn or get_conn()
    name = " ".join(w.capitalize() if w.islower() else w for w in name.strip().split())
    from app.hindi import has_devanagari
    cur = conn.execute("INSERT INTO customers (merchant_id, name, name_hi, created_at) VALUES (?, ?, ?, ?)",
                       (merchant_id, name, name if has_devanagari(name) else "", now_iso()))
    return _customer_dict(conn.execute("SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)).fetchone())


def set_opt_out(merchant_id: str, customer_id: int, opt_out: bool, actor: str = "admin") -> None:
    with transaction() as conn:
        conn.execute("UPDATE customers SET reminder_opt_out = ? WHERE merchant_id = ? AND id = ?",
                     (int(opt_out), merchant_id, customer_id))
        audit(merchant_id, actor, "customer.opt_out" if opt_out else "customer.opt_in", {"customer_id": customer_id}, conn)


# ------------------------------------------------------------------ udhaar ledger
def udhaar_balances(merchant_id: str) -> list[dict]:
    """One row per customer that has ledger entries, in the shape the tools expect."""
    rows = get_conn().execute(
        """
        SELECT c.id AS customer_id, c.name AS customer_name, c.name_hi AS customer_name_hi, c.aliases, c.phone, c.telegram_chat_id,
               c.reminder_opt_out, c.note,
               SUM(CASE WHEN e.kind = 'credit' THEN e.amount ELSE -e.amount END) AS outstanding,
               MIN(CASE WHEN e.kind = 'credit' THEN e.entry_date END)            AS credit_since,
               MAX(CASE WHEN e.kind = 'payment' THEN e.entry_date END)           AS last_payment_date
        FROM udhaar_entries e JOIN customers c ON c.id = e.customer_id
        WHERE e.merchant_id = ?
        GROUP BY c.id
        """,
        (merchant_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["aliases"] = json.loads(d["aliases"] or "[]")
        d["reminder_opt_out"] = bool(d["reminder_opt_out"])
        last = None
        if d["last_payment_date"]:
            last = get_conn().execute(
                "SELECT amount FROM udhaar_entries WHERE merchant_id = ? AND customer_id = ? AND kind = 'payment' "
                "AND entry_date = ? ORDER BY id DESC LIMIT 1", (merchant_id, d["customer_id"], d["last_payment_date"])
            ).fetchone()
        d["last_payment_amount"] = last["amount"] if last else 0
        out.append(d)
    return out


def add_udhaar_entry(merchant_id: str, customer_id: int, kind: str, amount: int, entry_date: str,
                     note: str = "", source: str = "voice", actor: str = "merchant", conn=None) -> int:
    if kind not in ("credit", "payment"):
        raise ValueError("kind must be credit or payment")
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("amount must be a positive integer")

    def _write(c):
        cur = c.execute(
            "INSERT INTO udhaar_entries (merchant_id, customer_id, kind, amount, entry_date, note, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (merchant_id, customer_id, kind, amount, entry_date, note, source, now_iso()))
        audit(merchant_id, actor, f"udhaar.{kind}", {"customer_id": customer_id, "amount": amount,
                                                      "entry_date": entry_date, "note": note, "entry_id": cur.lastrowid}, c)
        return cur.lastrowid

    if conn is not None:
        return _write(conn)
    with transaction() as c:
        return _write(c)


def udhaar_history(merchant_id: str, customer_id: int, limit: int = 20) -> list[dict]:
    rows = get_conn().execute(
        "SELECT kind, amount, entry_date, note, source FROM udhaar_entries WHERE merchant_id = ? AND customer_id = ? "
        "ORDER BY entry_date DESC, id DESC LIMIT ?", (merchant_id, customer_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ reminders
def last_sent_reminder(merchant_id: str, customer_id: int, conn=None) -> dict | None:
    """Last reminder delivered or being delivered right now (drives the cooldown)."""
    row = (conn or get_conn()).execute(
        "SELECT * FROM reminders WHERE merchant_id = ? AND customer_id = ? AND status IN ('sent', 'sending') "
        "ORDER BY id DESC LIMIT 1", (merchant_id, customer_id)).fetchone()
    return dict(row) if row else None


def claim_reminder_slot(merchant_id: str, customer_id: int, channel: str, message: str, cooldown_hours: int) -> int | None:
    """Atomically check the cooldown and reserve the send (status 'sending'). Returns the reminder id, or None
    if another reminder was sent/is being sent within the cooldown (e.g. two confirmations at the same moment)."""
    with transaction() as conn:
        last = last_sent_reminder(merchant_id, customer_id, conn)
        if last and hours_since(last["created_at"]) < cooldown_hours:
            return None
        cur = conn.execute(
            "INSERT INTO reminders (merchant_id, customer_id, channel, message, status, created_at) VALUES (?, ?, ?, ?, 'sending', ?)",
            (merchant_id, customer_id, channel, message, now_iso()))
        return cur.lastrowid


def finish_reminder(merchant_id: str, reminder_id: int, customer_id: int, channel: str, status: str,
                    via: str | None, error: str | None, actor: str = "merchant") -> None:
    with transaction() as conn:
        conn.execute("UPDATE reminders SET status = ?, via = ?, error = ? WHERE id = ? AND merchant_id = ?",
                     (status, via, error, reminder_id, merchant_id))
        audit(merchant_id, actor, f"reminder.{status}", {"customer_id": customer_id, "channel": channel, "via": via,
                                                         "error": error, "reminder_id": reminder_id}, conn)


def balance_of(merchant_id: str, customer_id: int, conn=None) -> int:
    return (conn or get_conn()).execute(
        "SELECT COALESCE(SUM(CASE kind WHEN 'credit' THEN amount ELSE -amount END), 0) FROM udhaar_entries "
        "WHERE merchant_id = ? AND customer_id = ?", (merchant_id, customer_id)).fetchone()[0]


def record_reminder(merchant_id: str, customer_id: int, channel: str, message: str, status: str,
                    via: str | None, error: str | None, actor: str = "merchant") -> int:
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (merchant_id, customer_id, channel, message, status, via, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (merchant_id, customer_id, channel, message, status, via, error, now_iso()))
        audit(merchant_id, actor, f"reminder.{status}", {"customer_id": customer_id, "channel": channel, "via": via,
                                                         "error": error, "reminder_id": cur.lastrowid}, conn)
        return cur.lastrowid


def hours_since(iso_ts: str) -> float:
    ts = datetime.fromisoformat(iso_ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


# ------------------------------------------------------------------ audit
def audit(merchant_id: str | None, actor: str, action: str, details: dict, conn=None) -> None:
    (conn or get_conn()).execute(
        "INSERT INTO audit_log (merchant_id, actor, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (merchant_id, actor, action, json.dumps(details, ensure_ascii=False, default=str), now_iso()))


def recent_audit(merchant_id: str, limit: int = 50) -> list[dict]:
    rows = get_conn().execute("SELECT * FROM audit_log WHERE merchant_id = ? ORDER BY id DESC LIMIT ?",
                              (merchant_id, limit)).fetchall()
    return [{**dict(r), "details": json.loads(r["details"])} for r in rows]


# ------------------------------------------------------------------ transactions (cached, read-mostly)
def _load_transactions(merchant_id: str) -> dict:
    conn = get_conn()
    items: dict[str, list] = {}
    for r in conn.execute(
        "SELECT i.txn_id, i.sku, i.name, i.category, i.qty, i.price FROM transaction_items i "
        "JOIN transactions t ON t.id = i.txn_id WHERE t.merchant_id = ?", (merchant_id,)):
        items.setdefault(r["txn_id"], []).append(
            {"sku": r["sku"], "name": r["name"], "category": r["category"], "qty": r["qty"], "price": r["price"]})
    txns = []
    for r in conn.execute(
        "SELECT t.*, c.name AS customer_name FROM transactions t LEFT JOIN customers c ON c.id = t.customer_id "
        "WHERE t.merchant_id = ? ORDER BY t.txn_date, t.txn_time", (merchant_id,)):
        txns.append({
            "txn_id": r["id"], "date": r["txn_date"], "time": r["txn_time"],
            "customer_name": r["customer_name"] or "Walk-in", "customer_type": r["customer_type"],
            "payer_vpa": r["payer_vpa"], "payment_mode": r["payment_mode"], "amount": r["amount"],
            "primary_category": r["primary_category"], "items": items.get(r["id"], []),
        })
    period = {"start": txns[0]["date"], "end": txns[-1]["date"], "days": None} if txns else {"start": None, "end": None, "days": 0}
    if txns:
        period["days"] = (date.fromisoformat(period["end"]) - date.fromisoformat(period["start"])).days + 1
    return {"transactions": txns, "period": period}


_load_locks: dict[tuple[str, str], threading.Lock] = {}


def merchant_transactions(merchant_id: str) -> dict:
    """Transactions are read-mostly: load once per merchant. Single-flight, so 20 parallel requests on a
    cold cache trigger one load, not twenty."""
    from app import config
    key = (str(config.DB_PATH), merchant_id)
    with _cache_lock:
        cached = _txn_cache.get(key)
        if cached is not None:
            return cached
        lock = _load_locks.setdefault(key, threading.Lock())
    with lock:
        with _cache_lock:
            cached = _txn_cache.get(key)
        if cached is None:
            cached = _load_transactions(merchant_id)
            with _cache_lock:
                _txn_cache[key] = cached
    return cached


def data_version(merchant_id: str) -> tuple:
    """Cheap fingerprint of everything the dashboard depends on (indexed MAX/COUNT queries).
    Read from the database itself, so changes made by other processes (admin CLI) are noticed too."""
    c = get_conn()
    return (
        c.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM udhaar_entries WHERE merchant_id = ?", (merchant_id,)).fetchone()[:],
        c.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM customers WHERE merchant_id = ?", (merchant_id,)).fetchone()[:],
        c.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM transactions WHERE merchant_id = ?", (merchant_id,)).fetchone()[:],
        c.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(SUM(status = 'pending'), 0) FROM incoming_payments "
                  "WHERE merchant_id = ?", (merchant_id,)).fetchone()[:],
    )


# ------------------------------------------------------------------ the dict the tools consume
def load_merchant_data(merchant_id: str | None = None) -> dict:
    mid = merchant_id or require_merchant()
    m = get_merchant(mid)
    if m is None:
        raise MerchantContextError(f"Unknown merchant '{mid}'")
    txn = merchant_transactions(mid)
    customers = list_customers(mid)
    catalog = [dict(r) for r in get_conn().execute(
        "SELECT sku, name, category, price FROM products WHERE merchant_id = ? ORDER BY sku", (mid,))]
    return {
        "store": {"store_name": m["store_name"], "owner_name": m["owner_name"], "location": m["location"],
                  "store_name_hi": m.get("store_name_hi") or m["store_name"],
                  "owner_name_hi": m.get("owner_name_hi") or m["owner_name"], "merchant_id": mid},
        "period": txn["period"],
        "catalog": catalog,
        "customers": customers,
        "regulars": [{"name": c["name"], "aliases": c["aliases"]} for c in customers if c["is_regular"]],
        "udhaar": udhaar_balances(mid),
        "transactions": txn["transactions"],
    }


# ------------------------------------------------------------------ agent sessions / conversation
def load_session(merchant_id: str, session_id: str, max_turns: int = 10) -> tuple[int, dict, list[dict]]:
    conn = get_conn()
    row = conn.execute("SELECT turn, pending FROM agent_sessions WHERE merchant_id = ? AND session_id = ?",
                       (merchant_id, session_id)).fetchone()
    if row is None:
        return 0, {}, []
    msgs = [{"role": r["role"], "content": json.loads(r["content"])} for r in conn.execute(
        "SELECT role, content FROM conversation_messages WHERE merchant_id = ? AND session_id = ? ORDER BY id",
        (merchant_id, session_id))]
    starts = [i for i, m in enumerate(msgs) if m["role"] == "user" and isinstance(m["content"], str)]
    if len(starts) > max_turns:
        msgs = msgs[starts[-max_turns]:]
    return row["turn"], json.loads(row["pending"] or "{}"), msgs


def _serialisable(content):
    if isinstance(content, list):
        return [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b for b in content]
    return content


def save_turn(merchant_id: str, session_id: str, turn: int, pending: dict, new_messages: list[dict]) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO agent_sessions (merchant_id, session_id, turn, pending, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (merchant_id, session_id) DO UPDATE SET turn = excluded.turn, pending = excluded.pending, "
            "updated_at = excluded.updated_at", (merchant_id, session_id, turn, json.dumps(pending), now_iso()))
        conn.executemany(
            "INSERT INTO conversation_messages (merchant_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            [(merchant_id, session_id, m["role"], json.dumps(_serialisable(m["content"]), ensure_ascii=False, default=str),
              now_iso()) for m in new_messages])


def reset_session(merchant_id: str, session_id: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM conversation_messages WHERE merchant_id = ? AND session_id = ?", (merchant_id, session_id))
        conn.execute("DELETE FROM agent_sessions WHERE merchant_id = ? AND session_id = ?", (merchant_id, session_id))


def conversation_transcript(merchant_id: str, session_id: str, limit: int = 50) -> list[dict]:
    """User questions + Munim's final replies, for restoring the chat after a page refresh."""
    out = []
    for r in get_conn().execute(
        "SELECT role, content FROM conversation_messages WHERE merchant_id = ? AND session_id = ? ORDER BY id",
        (merchant_id, session_id)):
        content = json.loads(r["content"])
        if r["role"] == "user" and isinstance(content, str):
            out.append({"role": "user", "text": content})
        elif r["role"] == "assistant" and isinstance(content, list):
            if any(b.get("type") == "tool_use" for b in content):
                continue
            text = " ".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            if text:
                out.append({"role": "munim", "text": text})
    return out[-limit:]


def list_conversations(merchant_id: str, limit: int = 30) -> list[dict]:
    """The merchant's past chats, newest first: first question, when, and how many questions."""
    rows = get_conn().execute(
        "SELECT session_id, MIN(id) AS first_id, MAX(created_at) AS last_at, COUNT(*) AS questions "
        "FROM conversation_messages WHERE merchant_id = ? AND role = 'user' AND substr(content, 1, 1) = '\"' "
        "GROUP BY session_id ORDER BY MAX(id) DESC LIMIT ?", (merchant_id, limit)).fetchall()
    out = []
    for r in rows:
        first = get_conn().execute("SELECT content FROM conversation_messages WHERE id = ?", (r["first_id"],)).fetchone()
        out.append({"session_id": r["session_id"], "title": json.loads(first["content"])[:80] if first else "",
                    "last_at": r["last_at"], "questions": r["questions"]})
    return out


def today_for(merchant_id: str) -> date:
    from app import config
    if config.DEMO_TODAY:
        return date.fromisoformat(config.DEMO_TODAY)
    if config.TODAY_MODE == "calendar":
        return date.today()
    end = merchant_transactions(merchant_id)["period"]["end"]
    return date.fromisoformat(end) if end else date.today()

