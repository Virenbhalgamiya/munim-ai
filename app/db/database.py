"""SQLite system of record (single local file, stdlib only).

One connection per thread (FastAPI runs sync endpoints in a threadpool). WAL mode so reads never
block the occasional write. Schema is versioned via PRAGMA user_version.
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app import config

SCHEMA_VERSION = 4   # v2: otp_requests · v3: Hindi (Devanagari) display names · v4: UPI repayment matching

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchants (
    id               TEXT PRIMARY KEY,
    store_name       TEXT NOT NULL,
    owner_name       TEXT NOT NULL,
    store_name_hi    TEXT NOT NULL DEFAULT '',
    owner_name_hi    TEXT NOT NULL DEFAULT '',
    location         TEXT NOT NULL DEFAULT '',
    phone            TEXT NOT NULL UNIQUE,
    pin_hash         TEXT NOT NULL,
    telegram_chat_id TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id      TEXT NOT NULL REFERENCES merchants(id),
    name             TEXT NOT NULL,
    name_hi          TEXT NOT NULL DEFAULT '',
    upi_vpa          TEXT NOT NULL DEFAULT '',
    aliases          TEXT NOT NULL DEFAULT '[]',
    phone            TEXT,
    telegram_chat_id TEXT,
    is_regular       INTEGER NOT NULL DEFAULT 0,
    reminder_opt_out INTEGER NOT NULL DEFAULT 0,
    note             TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    UNIQUE (merchant_id, name)
);

CREATE TABLE IF NOT EXISTS products (
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    sku         TEXT NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       INTEGER NOT NULL CHECK (price > 0),
    PRIMARY KEY (merchant_id, sku)
);

CREATE TABLE IF NOT EXISTS transactions (
    id               TEXT PRIMARY KEY,
    merchant_id      TEXT NOT NULL REFERENCES merchants(id),
    txn_date         TEXT NOT NULL,
    txn_time         TEXT NOT NULL,
    customer_id      INTEGER REFERENCES customers(id),
    customer_type    TEXT NOT NULL,
    payer_vpa        TEXT,
    payment_mode     TEXT NOT NULL DEFAULT 'UPI',
    amount           INTEGER NOT NULL CHECK (amount > 0),
    primary_category TEXT
);
CREATE INDEX IF NOT EXISTS ix_txn_merchant_date ON transactions (merchant_id, txn_date);

CREATE TABLE IF NOT EXISTS transaction_items (
    txn_id   TEXT NOT NULL REFERENCES transactions(id),
    sku      TEXT NOT NULL,
    name     TEXT NOT NULL,
    category TEXT NOT NULL,
    qty      INTEGER NOT NULL CHECK (qty > 0),
    price    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_items_txn ON transaction_items (txn_id);

-- Udhaar is an append-only ledger: outstanding = sum(credit) - sum(payment).
CREATE TABLE IF NOT EXISTS udhaar_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    kind        TEXT NOT NULL CHECK (kind IN ('credit', 'payment')),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    entry_date  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'voice',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_udhaar_merchant_customer ON udhaar_entries (merchant_id, customer_id);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    channel     TEXT NOT NULL,
    message     TEXT NOT NULL,
    status      TEXT NOT NULL,
    via         TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reminders_customer ON reminders (merchant_id, customer_id, created_at);

CREATE TABLE IF NOT EXISTS agent_sessions (
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    session_id  TEXT NOT NULL,
    turn        INTEGER NOT NULL DEFAULT 0,
    pending     TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (merchant_id, session_id)
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conv_session ON conversation_messages (merchant_id, session_id, id);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash  TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL REFERENCES merchants(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT NOT NULL,
    ip         TEXT,
    ok         INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_login_phone ON login_attempts (phone, created_at);

-- UPI payments received that are NOT a counter sale (no bill), e.g. a customer sending money from home.
-- If the payer owes udhaar, the merchant decides: repayment (linked to the khata) or a purchase (becomes a sale).
CREATE TABLE IF NOT EXISTS incoming_payments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id      TEXT NOT NULL REFERENCES merchants(id),
    provider_txn_id  TEXT NOT NULL,
    payer_vpa        TEXT NOT NULL DEFAULT '',
    payer_name       TEXT NOT NULL DEFAULT '',
    customer_id      INTEGER REFERENCES customers(id),
    amount           INTEGER NOT NULL CHECK (amount > 0),
    received_at      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',   -- pending | linked | dismissed | unmatched
    linked_amount    INTEGER NOT NULL DEFAULT 0,
    udhaar_entry_id  INTEGER REFERENCES udhaar_entries(id),
    source           TEXT NOT NULL DEFAULT 'webhook',   -- webhook | demo | seed
    decided_at       TEXT,
    UNIQUE (merchant_id, provider_txn_id)
);
CREATE INDEX IF NOT EXISTS ix_incoming_merchant_status ON incoming_payments (merchant_id, status);

-- One row per OTP sent. The code itself is never stored: Message Central checks it.
CREATE TABLE IF NOT EXISTS otp_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone           TEXT NOT NULL,
    merchant_id     TEXT NOT NULL REFERENCES merchants(id),
    verification_id TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | verified | expired | locked
    attempts        INTEGER NOT NULL DEFAULT 0,
    ip              TEXT,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_otp_phone ON otp_requests (phone, created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id TEXT,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_merchant ON audit_log (merchant_id, id);
"""

_local = threading.local()
_init_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit; explicit transactions below
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_conn() -> sqlite3.Connection:
    """Thread-local connection to config.DB_PATH (reconnects if the path changed, e.g. in tests)."""
    path = Path(config.DB_PATH)
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        if conn is not None:
            conn.close()
        conn = _connect(path)
        _local.conn, _local.path = conn, path
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, rollback on error."""
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def init_db() -> None:
    """Create / migrate the schema. Safe to call on every startup."""
    with _init_lock:
        conn = get_conn()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema v{version} is newer than this app (v{SCHEMA_VERSION})")
        if 0 < version < 4:   # existing database: add the new columns before CREATE INDEX etc.
            for table, column in (("merchants", "store_name_hi"), ("merchants", "owner_name_hi"), ("customers", "name_hi"),
                                  ("customers", "upi_vpa")):
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def healthcheck() -> bool:
    try:
        return get_conn().execute("SELECT 1").fetchone()[0] == 1
    except sqlite3.Error:
        return False
