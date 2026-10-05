"""Admin CLI (local).

  python -m app.manage init                       create DB + demo merchant (prints PIN once)
  python -m app.manage reset-demo                 delete the DB and re-create the demo store
  python -m app.manage create-merchant --id patil-kirana --store "Patil Kirana" --owner Sunita --phone 9822000002 [--pin 482913]
  python -m app.manage set-pin --phone 9822000001 [--pin 482913]
  python -m app.manage list-merchants
  python -m app.manage ledger --merchant sharma-gs
  python -m app.manage opt-out --merchant sharma-gs --customer Suresh [--undo]
  python -m app.manage audit --merchant sharma-gs [--limit 30]
  python -m app.manage upi-payment --customer Suresh --amount 500     stage a UPI repayment suggestion
"""
from __future__ import annotations

import argparse
import sys

from app import auth, config
from app.db import store
from app.db.database import close_conn, get_conn, init_db, transaction
from app.db.seed import bootstrap


def _merchant_or_exit(mid: str) -> dict:
    m = store.get_merchant(mid)
    if not m:
        sys.exit(f"No merchant '{mid}'. See: python -m app.manage list-merchants")
    return m


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="python -m app.manage", description="Munim AI admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("reset-demo")
    sub.add_parser("list-merchants")
    p = sub.add_parser("create-merchant")
    for a in ("--id", "--store", "--owner", "--phone"):
        p.add_argument(a, required=True)
    p.add_argument("--location", default="")
    p.add_argument("--pin")
    p = sub.add_parser("set-pin")
    p.add_argument("--phone", required=True)
    p.add_argument("--pin")
    p = sub.add_parser("set-phone")
    p.add_argument("--merchant", default=config.DEFAULT_MERCHANT_ID)
    p.add_argument("--phone", required=True)
    p = sub.add_parser("ledger")
    p.add_argument("--merchant", default=config.DEFAULT_MERCHANT_ID)
    p = sub.add_parser("opt-out")
    p.add_argument("--merchant", default=config.DEFAULT_MERCHANT_ID)
    p.add_argument("--customer", required=True)
    p.add_argument("--undo", action="store_true")
    p = sub.add_parser("upi-payment", help="record a UPI payment received (e.g. to stage a repayment in a demo)")
    p.add_argument("--merchant", default=config.DEFAULT_MERCHANT_ID)
    p.add_argument("--customer", required=True)
    p.add_argument("--amount", type=int, required=True)
    p = sub.add_parser("audit")
    p.add_argument("--merchant", default=config.DEFAULT_MERCHANT_ID)
    p.add_argument("--limit", type=int, default=30)
    args = ap.parse_args(argv)

    if args.cmd == "reset-demo":
        close_conn()
        for suffix in ("", "-wal", "-shm"):
            path = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
            if path.exists():
                path.unlink()
        store.invalidate_cache()
        bootstrap()
        print("Demo database re-created.")
        return 0
    if args.cmd == "init":
        bootstrap()
        print(f"Database ready at {config.DB_PATH}")
        return 0

    init_db()
    if args.cmd == "list-merchants":
        for r in get_conn().execute("SELECT id, store_name, owner_name, phone, is_active FROM merchants ORDER BY id"):
            print(f"{r['id']:<20} {r['store_name']:<28} {r['owner_name']:<12} {r['phone']}  {'active' if r['is_active'] else 'disabled'}")
    elif args.cmd == "create-merchant":
        pin = args.pin or auth.generate_pin()
        auth.create_merchant(args.id, args.store, args.owner, args.phone, pin, location=args.location)
        print(f"Created {args.id}. Login phone {auth.normalize_phone(args.phone)}  PIN {pin}")
    elif args.cmd == "set-pin":
        m = store.get_merchant_by_phone(auth.normalize_phone(args.phone))
        if not m:
            sys.exit("No merchant with that phone")
        pin = args.pin or auth.generate_pin()
        auth.set_pin(m["id"], pin)
        print(f"PIN updated for {m['id']}: {pin}  (all sessions logged out)")
    elif args.cmd == "set-phone":
        _merchant_or_exit(args.merchant)
        phone = auth.normalize_phone(args.phone)
        if not auth.PHONE_RE.match(phone):
            sys.exit("Phone must be a 10-digit Indian mobile number")
        other = store.get_merchant_by_phone(phone)
        if other and other["id"] != args.merchant:
            sys.exit(f"That phone already belongs to {other['id']}")
        with transaction() as conn:
            conn.execute("UPDATE merchants SET phone = ? WHERE id = ?", (phone, args.merchant))
            conn.execute("DELETE FROM auth_sessions WHERE merchant_id = ?", (args.merchant,))
            store.audit(args.merchant, "admin", "merchant.phone_changed", {"phone": phone}, conn)
        print(f"{args.merchant}: login phone is now {phone} (all sessions logged out)")
    elif args.cmd == "ledger":
        _merchant_or_exit(args.merchant)
        from app.agent.tools import udhaar_lookup
        with store.merchant_scope(args.merchant):
            r = udhaar_lookup()
        print(f"Total outstanding {r['total_outstanding_display']} across {r['customer_count']} customers")
        for c in r["customers"]:
            print(f"  {c['customer_name']:<16} {c['outstanding_display']:>9}  last paid {c['last_payment_date'] or '-'} "
                  f"({c['days_since_last_payment']} days){'  OVERDUE' if c['is_overdue'] else ''}")
    elif args.cmd == "opt-out":
        _merchant_or_exit(args.merchant)
        from app.agent.tools import match_customer
        matches = match_customer(args.customer, store.list_customers(args.merchant), key="name")
        if len(matches) != 1:
            sys.exit(f"Customer not found or ambiguous: {[m['name'] for m in matches]}")
        store.set_opt_out(args.merchant, matches[0]["id"], not args.undo)
        print(f"{matches[0]['name']}: reminders {'enabled' if args.undo else 'disabled (opted out)'}")
    elif args.cmd == "upi-payment":
        _merchant_or_exit(args.merchant)
        from app import payments
        from app.agent.tools import match_customer
        found = match_customer(args.customer, store.list_customers(args.merchant), key="name")
        vpa = found[0].get("upi_vpa", "") if len(found) == 1 else ""
        p = payments.record_incoming(args.merchant, args.amount, vpa, args.customer, None, "admin")
        print(f"UPI payment {p['amount_display']} from {p['customer_name']}: "
              f"{'waiting for your decision in the app (owes ' + p['outstanding_display'] + ')' if p['status'] == 'pending' else 'no udhaar, recorded only'}")
    elif args.cmd == "audit":
        _merchant_or_exit(args.merchant)
        for a in reversed(store.recent_audit(args.merchant, args.limit)):
            print(f"{a['created_at']}  {a['actor']:<9} {a['action']:<22} {a['details']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
