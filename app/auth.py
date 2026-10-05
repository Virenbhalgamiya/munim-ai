"""Merchant login: phone + PIN.

- PINs are stored as scrypt hashes (stdlib), never in plain text.
- Session tokens are random 256-bit values; only their SHA-256 is stored, so a leaked DB can't be replayed.
- Repeated wrong PINs lock the phone number for LOGIN_LOCKOUT_MINUTES.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from app import config
from app.db import store
from app.db.database import get_conn, now_iso, transaction

PHONE_RE = re.compile(r"^[6-9]\d{9}$")
PIN_RE = re.compile(r"^\d{4,6}$")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


class AuthError(Exception):
    """Wrong phone/PIN (message deliberately generic)."""


class LockedOut(AuthError):
    pass


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits


def validate_pin(pin: str) -> str:
    if not PIN_RE.match(pin or ""):
        raise ValueError("PIN must be 4-6 digits")
    if len(set(pin)) == 1 or pin in ("1234", "12345", "123456", "4321", "0000"):
        raise ValueError("PIN is too easy to guess")
    return pin


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt((pin or "").encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def generate_pin() -> str:
    while True:
        pin = f"{secrets.randbelow(10**6):06d}"
        try:
            return validate_pin(pin)
        except ValueError:
            continue


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _recent_failures(phone: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(minutes=config.LOGIN_LOCKOUT_MINUTES)).isoformat(timespec="seconds")
    last_ok = get_conn().execute(
        "SELECT MAX(created_at) FROM login_attempts WHERE phone = ? AND ok = 1", (phone,)).fetchone()[0]
    floor = max(since, last_ok or "")
    return get_conn().execute(
        "SELECT COUNT(*) FROM login_attempts WHERE phone = ? AND ok = 0 AND created_at > ?", (phone, floor)).fetchone()[0]


def login(phone: str, pin: str, ip: str | None = None) -> tuple[str, dict]:
    """Returns (session_token, merchant). Raises AuthError / LockedOut."""
    phone = normalize_phone(phone)
    if not PHONE_RE.match(phone):
        raise AuthError("फ़ोन नंबर या PIN गलत है")
    if _recent_failures(phone) >= config.LOGIN_MAX_FAILURES:
        raise LockedOut(f"बहुत बार गलत कोशिश। {config.LOGIN_LOCKOUT_MINUTES} मिनट बाद कोशिश कीजिए।")
    merchant = store.get_merchant_by_phone(phone)
    ok = bool(merchant and merchant["is_active"] and verify_pin(pin, merchant["pin_hash"]))
    with transaction() as conn:
        conn.execute("INSERT INTO login_attempts (phone, ip, ok, created_at) VALUES (?, ?, ?, ?)",
                     (phone, ip, int(ok), now_iso()))
        if not ok:
            store.audit(merchant["id"] if merchant else None, "anonymous", "auth.login_failed", {"phone": phone, "ip": ip}, conn)
            raise_after = True
        else:
            raise_after = False
            token = _new_session(conn, merchant, ip, "pin")
    if raise_after:
        raise AuthError("फ़ोन नंबर या PIN गलत है")
    return token, merchant


def _new_session(conn, merchant: dict, ip: str | None, method: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO auth_sessions (token_hash, merchant_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (_token_hash(token), merchant["id"], now_iso(), expires))
    conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now_iso(),))
    store.audit(merchant["id"], "merchant", "auth.login", {"ip": ip, "method": method}, conn)
    return token


def dev_login(merchant_id: str, ip: str | None) -> tuple[str, dict]:
    """DEV_MODE only (enforced by the caller): a session without phone/OTP, audited."""
    merchant = store.get_merchant(merchant_id)
    if not merchant or not merchant["is_active"]:
        raise AuthError("Developer mode: merchant not found")
    with transaction() as conn:
        token = _new_session(conn, merchant, ip, "dev_mode")
    return token, merchant


# ------------------------------------------------------------------ SMS OTP login (Message Central)
class TooSoon(AuthError):
    def __init__(self, message: str, wait_seconds: int):
        super().__init__(message)
        self.wait_seconds = wait_seconds


class OTPExpired(AuthError):
    pass


def _seconds_since(iso_ts: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_ts)).total_seconds()


def request_otp(phone: str, ip: str | None = None) -> dict:
    """Send a real SMS OTP to a registered merchant phone. Unknown numbers get the same answer but no SMS
    (no account discovery, and no free-credit burn on random numbers)."""
    from app.services.otp_service import get_otp_service

    phone = normalize_phone(phone)
    if not PHONE_RE.match(phone):
        raise AuthError("सही 10 अंकों का मोबाइल नंबर डालिए")
    if _recent_failures(phone) >= config.LOGIN_MAX_FAILURES:
        raise LockedOut(f"बहुत बार गलत कोशिश। {config.LOGIN_LOCKOUT_MINUTES} मिनट बाद कोशिश कीजिए।")
    answer = {"sent": True, "resend_after": config.OTP_RESEND_SECONDS, "valid_minutes": config.OTP_VALID_MINUTES}
    merchant = store.get_merchant_by_phone(phone)
    if not merchant or not merchant["is_active"]:
        store.audit(None, "anonymous", "auth.otp_unknown_phone", {"phone": phone, "ip": ip})
        return answer

    conn = get_conn()
    last = conn.execute("SELECT created_at FROM otp_requests WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone,)).fetchone()
    if last:
        waited = _seconds_since(last["created_at"])
        if waited < config.OTP_RESEND_SECONDS:
            wait = int(config.OTP_RESEND_SECONDS - waited) + 1
            raise TooSoon(f"नया OTP {wait} सेकंड बाद मँगाइए", wait)
    window = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
    recent = conn.execute("SELECT created_at FROM otp_requests WHERE phone = ? AND created_at > ? ORDER BY id",
                          (phone, window)).fetchall()
    if len(recent) >= config.OTP_MAX_SENDS_PER_10_MIN:
        wait = int(600 - _seconds_since(recent[0]["created_at"])) + 1
        raise TooSoon(f"बहुत बार OTP माँगा गया। {max(1, wait // 60)} मिनट बाद कोशिश कीजिए।", wait)

    verification_id = get_otp_service().send(phone)   # real SMS; raises OTPError on provider failure
    expires = (datetime.now(timezone.utc) + timedelta(minutes=config.OTP_VALID_MINUTES)).isoformat(timespec="seconds")
    with transaction() as c:
        c.execute("UPDATE otp_requests SET status = 'expired' WHERE phone = ? AND status = 'pending'", (phone,))
        c.execute("INSERT INTO otp_requests (phone, merchant_id, verification_id, ip, created_at, expires_at) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (phone, merchant["id"], verification_id, ip, now_iso(), expires))
        store.audit(merchant["id"], "anonymous", "auth.otp_sent", {"ip": ip}, c)
    return answer


def verify_otp(phone: str, code: str, ip: str | None = None) -> tuple[str, dict]:
    """Check the OTP with Message Central; on success create a session. Returns (token, merchant)."""
    from app.services.otp_service import OTPError, get_otp_service

    phone = normalize_phone(phone)
    code = (code or "").strip()
    if not PHONE_RE.match(phone) or not re.fullmatch(r"\d{4,8}", code):
        raise AuthError("OTP गलत है")
    if _recent_failures(phone) >= config.LOGIN_MAX_FAILURES:
        raise LockedOut(f"बहुत बार गलत कोशिश। {config.LOGIN_LOCKOUT_MINUTES} मिनट बाद कोशिश कीजिए।")
    req = get_conn().execute("SELECT * FROM otp_requests WHERE phone = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                             (phone,)).fetchone()
    if req is None:
        raise AuthError("पहले OTP मँगाइए")
    if req["expires_at"] < now_iso():
        with transaction() as c:
            c.execute("UPDATE otp_requests SET status = 'expired' WHERE id = ?", (req["id"],))
        raise OTPExpired("OTP की समय-सीमा खत्म हो गई, नया OTP मँगाइए")

    try:
        get_otp_service().verify(phone, req["verification_id"], code)
    except OTPError as e:
        if e.kind == "wrong":
            attempts = req["attempts"] + 1
            locked = attempts >= config.OTP_MAX_ATTEMPTS
            with transaction() as c:
                c.execute("UPDATE otp_requests SET attempts = ?, status = ? WHERE id = ?",
                          (attempts, "locked" if locked else "pending", req["id"]))
                c.execute("INSERT INTO login_attempts (phone, ip, ok, created_at) VALUES (?, ?, 0, ?)", (phone, ip, now_iso()))
                store.audit(req["merchant_id"], "anonymous", "auth.otp_wrong", {"ip": ip, "attempts": attempts}, c)
            if locked:
                raise LockedOut("बहुत बार गलत OTP। नया OTP मँगाइए।") from None
            left = config.OTP_MAX_ATTEMPTS - attempts
            raise AuthError(f"OTP गलत है ({left} कोशिश बाकी)") from None
        if e.kind == "expired":
            with transaction() as c:
                c.execute("UPDATE otp_requests SET status = 'expired' WHERE id = ?", (req["id"],))
            raise OTPExpired(str(e)) from None
        raise

    merchant = store.get_merchant(req["merchant_id"])
    with transaction() as c:
        c.execute("UPDATE otp_requests SET status = 'verified' WHERE id = ?", (req["id"],))
        c.execute("INSERT INTO login_attempts (phone, ip, ok, created_at) VALUES (?, ?, 1, ?)", (phone, ip, now_iso()))
        token = _new_session(c, merchant, ip, "otp")
    return token, merchant


def merchant_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    row = get_conn().execute(
        "SELECT m.* FROM auth_sessions s JOIN merchants m ON m.id = s.merchant_id "
        "WHERE s.token_hash = ? AND s.expires_at > ? AND m.is_active = 1", (_token_hash(token), now_iso())).fetchone()
    return dict(row) if row else None


def logout(token: str | None) -> None:
    if token:
        with transaction() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))


def set_pin(merchant_id: str, pin: str, actor: str = "admin") -> None:
    validate_pin(pin)
    with transaction() as conn:
        conn.execute("UPDATE merchants SET pin_hash = ? WHERE id = ?", (hash_pin(pin), merchant_id))
        conn.execute("DELETE FROM auth_sessions WHERE merchant_id = ?", (merchant_id,))  # log out everywhere
        store.audit(merchant_id, actor, "auth.pin_changed", {}, conn)


def create_merchant(merchant_id: str, store_name: str, owner_name: str, phone: str, pin: str,
                    location: str = "", telegram_chat_id: str | None = None, conn=None) -> dict:
    phone = normalize_phone(phone)
    if not re.match(r"^[a-z0-9-]{3,40}$", merchant_id):
        raise ValueError("merchant id: 3-40 chars, lowercase letters, digits, '-'")
    if not PHONE_RE.match(phone):
        raise ValueError("phone must be a 10-digit Indian mobile number")
    validate_pin(pin)

    def _write(c):
        c.execute("INSERT INTO merchants (id, store_name, owner_name, location, phone, pin_hash, telegram_chat_id, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (merchant_id, store_name, owner_name, location, phone, hash_pin(pin), telegram_chat_id, now_iso()))
        store.audit(merchant_id, "admin", "merchant.created", {"phone": phone}, c)

    if conn is not None:
        _write(conn)
    else:
        with transaction() as c:
            _write(c)
    return store.get_merchant(merchant_id)
