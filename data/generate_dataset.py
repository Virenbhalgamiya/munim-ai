"""
Synthetic dataset generator for Munim AI demo — "Sharma General Store" (owner: Ramesh).

The data is deliberately SHAPED, not random, so demo insights are real and reproducible:
  * 60 days of UPI sales ending the day before the hackathon (default 2026-10-02).
  * Every Tuesday is ~25-35% below that week's average (enforced + verified below).
  * 10 named regulars; 3 of them stop coming in the last ~19 days ("lapsed").
  * Udhaar (credit) ledger with "Suresh" owing Rs 1,850, last paid 19 days ago (demo reminder target).
  * Category mix where household items + a couple of SKUs clearly under-perform.

Run:  python data/generate_dataset.py            (writes CSV + JSON next to this file)
      python data/generate_dataset.py --end-date 2026-10-02 --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent

STORE = {
    "store_name": "Sharma General Store",
    "owner_name": "Ramesh",
    "owner_name_hi": "रमेश",
    "location": "Kothrud, Pune",
    "paytm_merchant_id": "SHARMA-GS-0421",
}

# --- Product catalogue: (sku, name, category, price, weight) ------------------
# weight = relative pick probability. Low weights are the "not selling" answers.
CATALOG = [
    # groceries
    ("GR01", "Aashirvaad Atta 5kg", "groceries", 285, 6),
    ("GR02", "India Gate Basmati 1kg", "groceries", 145, 5),
    ("GR03", "Toor Dal 1kg", "groceries", 160, 6),
    ("GR04", "Fortune Sunflower Oil 1L", "groceries", 155, 6),
    ("GR05", "Sugar 1kg", "groceries", 48, 8),
    ("GR06", "Tata Salt 1kg", "groceries", 28, 6),
    ("GR07", "Poha 500g", "groceries", 40, 5),
    ("GR08", "Tata Tea Gold 250g", "groceries", 150, 5),
    # snacks
    ("SN01", "Parle-G 250g", "snacks", 25, 10),
    ("SN02", "Lay's Classic 52g", "snacks", 20, 9),
    ("SN03", "Haldiram Bhujia 200g", "snacks", 55, 6),
    ("SN04", "Dairy Milk 50g", "snacks", 50, 6),
    ("SN05", "Maggi 4-pack", "snacks", 56, 7),
    ("SN06", "Kurkure Masala 90g", "snacks", 20, 7),
    ("SN07", "Real Fruit Juice 1L", "snacks", 120, 1),       # slow mover
    # dairy
    ("DA01", "Amul Taaza Milk 500ml", "dairy", 27, 14),
    ("DA02", "Amul Curd 400g", "dairy", 35, 6),
    ("DA03", "Amul Butter 100g", "dairy", 58, 4),
    ("DA04", "Paneer 200g", "dairy", 90, 4),
    ("DA05", "Bread (Modern) 400g", "dairy", 40, 8),
    # household (deliberately weak category)
    ("HH01", "Surf Excel 1kg", "household", 140, 1.2),
    ("HH02", "Lifebuoy Soap 4-pack", "household", 150, 1.2),
    ("HH03", "Colgate 200g", "household", 110, 1.2),
    ("HH04", "Vim Bar 3-pack", "household", 60, 1.0),
    ("HH05", "Harpic 500ml", "household", 99, 0.25),        # barely sells
    ("HH06", "Good Knight Refill", "household", 85, 0.4),
]

# --- Regular customers ----------------------------------------------------------
# visit_prob = chance they transact on a given day (~0.5 = 3-4 visits/week)
REGULARS = [
    {"name": "Suresh",         "aliases": ["सुरेश", "suresh bhai", "suresh ji"], "visit_prob": 0.55, "lapsed": False, "basket": (60, 350)},
    {"name": "Anita Desai",    "aliases": ["अनीता", "anita", "anita ji"],        "visit_prob": 0.60, "lapsed": True,  "basket": (120, 600)},
    {"name": "Vikram Joshi",   "aliases": ["विक्रम", "vikram"],                   "visit_prob": 0.50, "lapsed": True,  "basket": (80, 450)},
    {"name": "Kavita Shinde",  "aliases": ["कविता", "kavita", "kavita tai"],     "visit_prob": 0.55, "lapsed": True,  "basket": (60, 300)},
    {"name": "Meena Patil",    "aliases": ["मीना", "meena", "meena tai"],        "visit_prob": 0.60, "lapsed": False, "basket": (50, 280)},
    {"name": "Rajesh Gupta",   "aliases": ["राजेश", "rajesh"],                    "visit_prob": 0.50, "lapsed": False, "basket": (100, 700)},
    {"name": "Sunita Yadav",   "aliases": ["सुनीता", "sunita"],                   "visit_prob": 0.55, "lapsed": False, "basket": (40, 250)},
    {"name": "Amit Verma",     "aliases": ["अमित", "amit"],                       "visit_prob": 0.45, "lapsed": False, "basket": (30, 200)},
    {"name": "Deepak Mehta",   "aliases": ["दीपक", "deepak"],                     "visit_prob": 0.45, "lapsed": False, "basket": (80, 500)},
    {"name": "Farhan Shaikh",  "aliases": ["फरहान", "farhan"],                    "visit_prob": 0.50, "lapsed": False, "basket": (40, 260)},
]

LAPSE_AFTER_DAY_INDEX = 41   # lapsed regulars stop from day index 41 => last 19 days empty
DEFAULT_END_DATE = "2026-10-02"  # day before the 3 Oct 2026 hackathon
N_DAYS = 60


def _pick_items(rng: random.Random, lo: int, hi: int) -> tuple[list[dict], int]:
    """Build a basket of catalogue items whose total lands in [lo, hi] (and 20..800)."""
    lo, hi = max(20, lo), min(800, hi)
    target = rng.randint(lo, hi)
    weights = [c[4] for c in CATALOG]
    items: dict[str, dict] = {}
    total = 0
    for _ in range(12):
        sku, name, cat, price, _w = rng.choices(CATALOG, weights=weights, k=1)[0]
        if total + price > hi:
            continue
        it = items.setdefault(sku, {"sku": sku, "name": name, "category": cat, "qty": 0, "price": price})
        it["qty"] += 1
        total += price
        if total >= target:
            break
    if not items:  # fallback: single cheapest dairy/snack item
        sku, name, cat, price, _w = CATALOG[15]
        items[sku] = {"sku": sku, "name": name, "category": cat, "qty": 1, "price": price}
        total = price
    return list(items.values()), total


def _walkin_basket(rng: random.Random) -> tuple[int, int]:
    # Typical kirana basket: mostly small, occasionally a monthly-ration run.
    r = rng.random()
    if r < 0.55:
        return 20, 120
    if r < 0.88:
        return 100, 350
    return 300, 800


def _time_of_day(rng: random.Random, d: date) -> str:
    hour = rng.choices(range(7, 22), weights=[5, 9, 8, 6, 5, 4, 3, 3, 4, 6, 9, 10, 9, 6, 3], k=1)[0]
    return f"{hour:02d}:{rng.randint(0, 59):02d}"


def _make_txn(rng, d: date, customer: str, ctype: str, lo: int, hi: int, counter: list[int]) -> dict:
    items, amount = _pick_items(rng, lo, hi)
    counter[0] += 1
    primary = max(items, key=lambda i: i["price"] * i["qty"])["category"]
    vpa = f"{customer.split()[0].lower()}@paytm" if ctype == "regular" else f"user{rng.randint(1000, 9999)}@{rng.choice(['paytm', 'ybl', 'okaxis', 'oksbi'])}"
    return {
        "txn_id": f"TXN{d.strftime('%Y%m%d')}{counter[0]:05d}",
        "date": d.isoformat(),
        "time": _time_of_day(rng, d),
        "weekday": d.strftime("%A"),
        "customer_name": customer,
        "customer_type": ctype,
        "payer_vpa": vpa,
        "payment_mode": "UPI",
        "amount": amount,
        "primary_category": primary,
        "items": items,
    }


def _week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def generate(end_date: date, seed: int = 42) -> dict:
    rng = random.Random(seed)
    start = end_date - timedelta(days=N_DAYS - 1)
    days = [start + timedelta(days=i) for i in range(N_DAYS)]
    counter = [0]
    by_day: dict[date, list[dict]] = {}

    def regular_txns(idx: int, d: date) -> list[dict]:
        out = []
        for r in REGULARS:
            if r["lapsed"] and idx >= LAPSE_AFTER_DAY_INDEX:
                continue
            # force active regulars to show up in the final 2 days so they never look lapsed
            forced = (not r["lapsed"]) and idx == N_DAYS - 1 and r["name"] in ("Suresh", "Meena Patil", "Rajesh Gupta", "Sunita Yadav", "Amit Verma")
            if forced or rng.random() < r["visit_prob"]:
                out.append(_make_txn(rng, d, r["name"], "regular", *r["basket"], counter))
        return out

    # Pass 1: all non-Tuesday days.
    for idx, d in enumerate(days):
        if d.weekday() == 1:
            continue
        txns = regular_txns(idx, d)
        n_total = rng.randint(58, 76) if d.weekday() >= 5 else rng.randint(50, 70)
        while len(txns) < n_total:
            txns.append(_make_txn(rng, d, "Walk-in", "walk-in", *_walkin_basket(rng), counter))
        by_day[d] = txns

    # Pass 2: Tuesdays, filled until they sit ~30% below their week's average.
    for idx, d in enumerate(days):
        if d.weekday() != 1:
            continue
        others = [sum(t["amount"] for t in by_day[o]) for o in days if o != d and _week_key(o) == _week_key(d) and o in by_day]
        k = len(others) + 1
        s_others = sum(others)
        target_ratio = rng.uniform(0.685, 0.71)  # Tuesday / week-average
        target_total = target_ratio * s_others / (k - target_ratio)
        txns = regular_txns(idx, d)
        while sum(t["amount"] for t in txns) < target_total or len(txns) < 30:
            lo, hi = _walkin_basket(rng)
            txns.append(_make_txn(rng, d, "Walk-in", "walk-in", lo, min(hi, 350), counter))
            if len(txns) >= 80:
                break
        by_day[d] = txns

    transactions = []
    for d in days:
        transactions.extend(sorted(by_day[d], key=lambda t: t["time"]))

    # Udhaar ledger: last_payment_date relative to end_date.
    def ago(n: int) -> str:
        return (end_date - timedelta(days=n)).isoformat()

    udhaar = [
        {"customer_name": "Suresh", "aliases": ["सुरेश", "suresh bhai", "suresh ji"], "outstanding": 1850,
         "credit_since": ago(34), "last_payment_date": ago(19), "last_payment_amount": 500,
         "phone": "+91-98220-11850", "telegram_chat_id": None,
         "note": "Ramesh ke purane dost; monthly ration udhaar pe leta hai, payment mein der karta hai"},
        {"customer_name": "Rajesh Gupta", "aliases": ["राजेश", "rajesh"], "outstanding": 2400,
         "credit_since": ago(12), "last_payment_date": ago(6), "last_payment_amount": 1000,
         "phone": "+91-98220-22400", "telegram_chat_id": None, "note": "Tiffin service, bulk atta-dal; pays every week"},
        {"customer_name": "Deepak Mehta", "aliases": ["दीपक", "deepak"], "outstanding": 1150,
         "credit_since": ago(15), "last_payment_date": ago(8), "last_payment_amount": 400,
         "phone": "+91-98220-31150", "telegram_chat_id": None, "note": ""},
        {"customer_name": "Meena Patil", "aliases": ["मीना", "meena", "meena tai"], "outstanding": 640,
         "credit_since": ago(9), "last_payment_date": ago(4), "last_payment_amount": 300,
         "phone": "+91-98220-40640", "telegram_chat_id": None, "note": ""},
        {"customer_name": "Sunita Yadav", "aliases": ["सुनीता", "sunita"], "outstanding": 420,
         "credit_since": ago(5), "last_payment_date": ago(2), "last_payment_amount": 200,
         "phone": "+91-98220-50420", "telegram_chat_id": None, "note": ""},
        {"customer_name": "Mohan Chawla", "aliases": ["मोहन", "mohan"], "outstanding": 260,
         "credit_since": ago(11), "last_payment_date": ago(9), "last_payment_amount": 100,
         "phone": "+91-98220-60260", "telegram_chat_id": None, "note": "Occasional customer"},
    ]

    regulars_meta = [{k: r[k] for k in ("name", "aliases", "lapsed")} for r in REGULARS]

    return {
        "store": STORE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": {"start": start.isoformat(), "end": end_date.isoformat(), "days": N_DAYS},
        "catalog": [{"sku": s, "name": n, "category": c, "price": p} for s, n, c, p, _w in CATALOG],
        "regulars": regulars_meta,
        "udhaar": udhaar,
        "transactions": transactions,
    }


def verify(dataset: dict) -> list[str]:
    """Sanity checks on the baked-in patterns. Returns human-readable report lines."""
    df = pd.DataFrame(dataset["transactions"])
    df["date"] = pd.to_datetime(df["date"])
    daily = df.groupby("date").agg(total=("amount", "sum"), count=("amount", "size")).reset_index()
    daily["week"] = daily["date"].dt.isocalendar().week
    daily["weekday"] = daily["date"].dt.day_name()
    lines = []
    assert daily["count"].between(30, 80).all(), "daily txn count out of 30-80"
    assert df["amount"].between(20, 800).all(), "txn amount out of 20-800"
    for _, row in daily[daily["weekday"] == "Tuesday"].iterrows():
        week_avg = daily[daily["week"] == row["week"]]["total"].mean()
        dip = 1 - row["total"] / week_avg
        lines.append(f"  Tuesday {row['date'].date()}: Rs {row['total']:,} vs week avg Rs {week_avg:,.0f}  -> {dip:.0%} below")
        assert 0.25 <= dip <= 0.35, f"Tuesday dip out of band on {row['date'].date()}: {dip:.2%}"
    by_wd = daily.groupby("weekday")["total"].mean().reindex(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
    lines.append("  Avg sales by weekday: " + ", ".join(f"{k[:3]} Rs {v:,.0f}" for k, v in by_wd.items()))
    end = pd.Timestamp(dataset["period"]["end"])
    for r in dataset["regulars"]:
        last = df[df["customer_name"] == r["name"]]["date"].max()
        lines.append(f"  {r['name']:<14} last visit {last.date()} ({(end - last).days} days ago){'  <- LAPSED' if r['lapsed'] else ''}")
    return lines


def write(dataset: dict, out_dir: Path = DATA_DIR) -> None:
    (out_dir / "merchant_transactions.json").write_text(json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = []
    for t in dataset["transactions"]:
        row = {k: v for k, v in t.items() if k != "items"}
        row["items"] = "; ".join(f"{i['qty']}x {i['name']}" for i in t["items"])
        row["categories"] = ",".join(sorted({i["category"] for i in t["items"]}))
        rows.append(row)
    pd.DataFrame(rows).to_csv(out_dir / "merchant_transactions.csv", index=False, encoding="utf-8")
    pd.DataFrame([{k: v for k, v in u.items() if k != "aliases"} for u in dataset["udhaar"]]).to_csv(
        out_dir / "udhaar_ledger.csv", index=False, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end-date", default=DEFAULT_END_DATE, help="last day of data (YYYY-MM-DD)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    ds = generate(date.fromisoformat(args.end_date), args.seed)
    report = verify(ds)
    write(ds)
    print(f"Wrote {len(ds['transactions'])} transactions ({ds['period']['start']} -> {ds['period']['end']}) to {DATA_DIR}")
    print("\n".join(report))


if __name__ == "__main__":
    main()
