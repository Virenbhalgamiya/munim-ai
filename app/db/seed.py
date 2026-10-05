"""Load the generated demo dataset (data/merchant_transactions.json) into SQLite as one merchant."""
from __future__ import annotations

import json
from pathlib import Path

from app import config
from app.auth import create_merchant, generate_pin
from app.db import store
from app.db.database import get_conn, init_db, now_iso, transaction
from app.hindi import DEMO_NAMES_HI, DEMO_STORE_HI

DEMO_MERCHANT_ID = "sharma-gs"


def is_empty() -> bool:
    return get_conn().execute("SELECT COUNT(*) FROM merchants").fetchone()[0] == 0


def seed_demo(json_path: Path | None = None, merchant_id: str = DEMO_MERCHANT_ID, phone: str | None = None,
              pin: str | None = None) -> str | None:
    """Idempotent. Returns the PIN if the merchant was created now, else None."""
    init_db()
    if store.get_merchant(merchant_id):
        return None
    path = Path(json_path or config.DATASET_PATH)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: python data/generate_dataset.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    pin = pin or config.SEED_MERCHANT_PIN or generate_pin()
    s = data["store"]
    created = now_iso()

    with transaction() as conn:
        create_merchant(merchant_id, s["store_name"], s["owner_name"], phone or config.SEED_MERCHANT_PHONE, pin,
                        location=s.get("location", ""), telegram_chat_id=config.TELEGRAM_CHAT_ID or None, conn=conn)

        conn.executemany("INSERT INTO products (merchant_id, sku, name, category, price) VALUES (?, ?, ?, ?, ?)",
                         [(merchant_id, p["sku"], p["name"], p["category"], p["price"]) for p in data["catalog"]])

        # customers = regulars + anyone in the udhaar ledger
        people: dict[str, dict] = {}
        for r in data["regulars"]:
            people[r["name"]] = {"aliases": r["aliases"], "is_regular": 1, "phone": None, "note": ""}
        for u in data["udhaar"]:
            p = people.setdefault(u["customer_name"], {"aliases": u.get("aliases", []), "is_regular": 0})
            p.update(phone=u.get("phone"), note=u.get("note", ""), telegram_chat_id=u.get("telegram_chat_id"))
        ids: dict[str, int] = {}
        for name, p in people.items():
            cur = conn.execute(
                "INSERT INTO customers (merchant_id, name, name_hi, upi_vpa, aliases, phone, telegram_chat_id, is_regular, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (merchant_id, name, DEMO_NAMES_HI.get(name, ""), _vpa(name), json.dumps(p["aliases"], ensure_ascii=False), p.get("phone"),
                 p.get("telegram_chat_id"), p["is_regular"], p.get("note", ""), created))
            ids[name] = cur.lastrowid

        conn.executemany(
            "INSERT INTO transactions (id, merchant_id, txn_date, txn_time, customer_id, customer_type, payer_vpa, "
            "payment_mode, amount, primary_category) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(f"{merchant_id}:{t['txn_id']}", merchant_id, t["date"], t["time"], ids.get(t["customer_name"]),
              t["customer_type"], t["payer_vpa"], t["payment_mode"], t["amount"], t["primary_category"])
             for t in data["transactions"]])
        conn.executemany(
            "INSERT INTO transaction_items (txn_id, sku, name, category, qty, price) VALUES (?, ?, ?, ?, ?, ?)",
            [(f"{merchant_id}:{t['txn_id']}", i["sku"], i["name"], i["category"], i["qty"], i["price"])
             for t in data["transactions"] for i in t["items"]])

        # Ledger: one credit (outstanding + last payment) on credit_since, then the last payment.
        for u in data["udhaar"]:
            cid = ids[u["customer_name"]]
            store.add_udhaar_entry(merchant_id, cid, "credit", u["outstanding"] + u["last_payment_amount"],
                                   u["credit_since"], "opening balance", "seed", "seed", conn=conn)
            if u["last_payment_amount"]:
                store.add_udhaar_entry(merchant_id, cid, "payment", u["last_payment_amount"], u["last_payment_date"],
                                       "", "seed", "seed", conn=conn)
        conn.execute("UPDATE merchants SET store_name_hi = ?, owner_name_hi = ? WHERE id = ?",
                     (DEMO_STORE_HI["store_name_hi"], DEMO_STORE_HI["owner_name_hi"], merchant_id))
    store.invalidate_cache(merchant_id)
    return pin


def _vpa(name: str) -> str:
    """Synthetic UPI ID, the same one the generated transactions use as payer_vpa."""
    return f"{name.split()[0].lower()}@paytm"


DEMO_REPAYMENT = {"customer": "Rajesh Gupta", "amount": 1000, "txn": "seed-upi-rajesh-1000"}


def seed_demo_repayment(merchant_id: str = DEMO_MERCHANT_ID) -> None:
    """One realistic pending suggestion for the demo: Rajesh sent ₹1,000 by UPI (no bill) and owes udhaar."""
    from app.payments import record_incoming
    if get_conn().execute("SELECT COUNT(*) FROM incoming_payments WHERE merchant_id = ?", (merchant_id,)).fetchone()[0]:
        return
    if not store.get_merchant(merchant_id):
        return
    record_incoming(merchant_id, DEMO_REPAYMENT["amount"], _vpa(DEMO_REPAYMENT["customer"]), DEMO_REPAYMENT["customer"],
                    DEMO_REPAYMENT["txn"], source="seed", notify=False)


def backfill_hindi_names() -> None:
    """Databases created before schema v3/v4: fill Devanagari names and UPI IDs for the demo store."""
    with transaction() as conn:
        for (cid, name) in conn.execute("SELECT id, name FROM customers WHERE merchant_id = ? AND upi_vpa = ''",
                                        (DEMO_MERCHANT_ID,)).fetchall():
            conn.execute("UPDATE customers SET upi_vpa = ? WHERE id = ?", (_vpa(name), cid))
        conn.execute("UPDATE merchants SET store_name_hi = ?, owner_name_hi = ? WHERE id = ? AND store_name_hi = ''",
                     (DEMO_STORE_HI["store_name_hi"], DEMO_STORE_HI["owner_name_hi"], DEMO_MERCHANT_ID))
        for name, hi in DEMO_NAMES_HI.items():
            conn.execute("UPDATE customers SET name_hi = ? WHERE merchant_id = ? AND name = ? AND name_hi = ''",
                         (hi, DEMO_MERCHANT_ID, name))


def bootstrap(verbose: bool = True) -> None:
    """Create schema; seed the demo store into an empty DB if SEED_DEMO_DATA is on."""
    init_db()
    backfill_hindi_names()
    if config.SEED_DEMO_DATA and is_empty():
        pin = seed_demo()
        if verbose and pin:
            # Printed once to the local console only (never logged to file).
            print("\n" + "=" * 64)
            print(f"  Demo merchant created: {DEMO_MERCHANT_ID}")
            print(f"  Login phone: {config.SEED_MERCHANT_PHONE}   PIN: {pin}")
            print("  Change it any time:  python -m app.manage set-pin --phone", config.SEED_MERCHANT_PHONE)
            print("=" * 64 + "\n", flush=True)
    if config.SEED_DEMO_DATA:
        seed_demo_repayment()
