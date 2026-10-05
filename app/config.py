"""Central config, read from env (.env supported). Everything runs on real services:

    AI         : Groq free tier (default) or Gemini free tier, Claude optional (LLM_PROVIDER)
    Voice      : browser speech (Chrome/Edge) on the web page
    Reminders  : Telegram Bot API
    Data       : local SQLite database (synthetic demo data loaded on first start)
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


# --- AI -------------------------------------------------------------------------
LLM_PROVIDER: str = _str("LLM_PROVIDER", "groq").lower()   # groq | gemini | anthropic
GROQ_API_KEY: str = _str("GROQ_API_KEY")
GEMINI_API_KEY: str = _str("GEMINI_API_KEY")
ANTHROPIC_API_KEY: str = _str("ANTHROPIC_API_KEY")
_API_KEYS = {"groq": ("GROQ_API_KEY", GROQ_API_KEY), "gemini": ("GEMINI_API_KEY", GEMINI_API_KEY),
             "anthropic": ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)}

_DEFAULT_MODELS = {"groq": "openai/gpt-oss-120b", "gemini": "gemini-2.5-flash", "anthropic": "claude-sonnet-5-5"}
LLM_MODEL: str = _str("LLM_MODEL", _DEFAULT_MODELS.get(LLM_PROVIDER, ""))
LLM_MAX_TOKENS: int = int(_str("LLM_MAX_TOKENS", "4096" if LLM_PROVIDER == "anthropic" else "700"))
# Free tiers cap tokens/minute PER MODEL (Groq: 8,000 TPM). On a rate limit, the next real model is tried.
LLM_FALLBACK_MODELS: list[str] = [m.strip() for m in _str(
    "LLM_FALLBACK_MODELS", "openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-safeguard-20b" if LLM_PROVIDER == "groq" else "").split(",") if m.strip()]
# With LLM_PROVIDER=groq and a GEMINI_API_KEY, these Gemini models are the last resort when every Groq model is limited.
GEMINI_BACKUP_MODELS: list[str] = [m.strip() for m in _str("GEMINI_BACKUP_MODELS", "gemini-2.5-flash,gemini-flash-lite-latest").split(",") if m.strip()]
LLM_HISTORY_TURNS: int = int(_str("LLM_HISTORY_TURNS", "4"))     # groq/gemini: only last N merchant turns are sent
LLM_TEMPERATURE: float = float(_str("LLM_TEMPERATURE", "0.2"))   # groq/gemini only
LLM_EFFORT: str = _str("LLM_EFFORT", "low")                      # anthropic only
LLM_USE_FALLBACKS: bool = _bool("LLM_USE_FALLBACKS", True)       # anthropic only

# --- Reminders (Telegram Bot API) ----------------------------------------------
TELEGRAM_BOT_TOKEN: str = _str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _str("TELEGRAM_CHAT_ID")
REMINDER_COOLDOWN_HOURS: int = int(_str("REMINDER_COOLDOWN_HOURS", "72"))
MAX_UDHAAR_ENTRY: int = int(_str("MAX_UDHAAR_ENTRY", "100000"))  # ₹ per single voice entry

# --- Login: real SMS OTP (Message Central VerifyNow) --------------------------------
LOGIN_METHOD: str = _str("LOGIN_METHOD", "otp").lower()      # otp (SMS) | pin
MESSAGECENTRAL_CUSTOMER_ID: str = _str("MESSAGECENTRAL_CUSTOMER_ID")
MESSAGECENTRAL_PASSWORD: str = _str("MESSAGECENTRAL_PASSWORD")   # used to generate the auth token
MESSAGECENTRAL_EMAIL: str = _str("MESSAGECENTRAL_EMAIL")
MESSAGECENTRAL_AUTH_TOKEN: str = _str("MESSAGECENTRAL_AUTH_TOKEN")  # optional: paste a token instead of password
OTP_RESEND_SECONDS: int = int(_str("OTP_RESEND_SECONDS", "60"))
OTP_MAX_SENDS_PER_10_MIN: int = int(_str("OTP_MAX_SENDS_PER_10_MIN", "3"))
OTP_VALID_MINUTES: int = int(_str("OTP_VALID_MINUTES", "5"))
OTP_MAX_ATTEMPTS: int = int(_str("OTP_MAX_ATTEMPTS", "5"))

# --- Voice output ------------------------------------------------------------------
VOICE_ENGINE: str = _str("VOICE_ENGINE", "neural").lower()   # neural (Microsoft neural voices, free) | browser
TTS_RATE: str = _str("TTS_RATE", "-4%")                       # speaking speed, e.g. "-10%" slower, "+5%" faster
TTS_TIMEOUT_SECONDS: float = float(_str("TTS_TIMEOUT_SECONDS", "12"))

# --- Incoming UPI payments (repayment matching) -----------------------------------
UPI_WEBHOOK_SECRET: str = _str("UPI_WEBHOOK_SECRET")   # enables POST /api/webhooks/upi (header X-Webhook-Secret)

# --- Developer mode (local only): skip phone + OTP login --------------------------
DEV_MODE: bool = _bool("DEV_MODE", False)
# Hosted public demo (e.g. on Render): anyone with the link lands on the demo shop without login, and
# reminders / alerts are simulated (nothing is sent to a real Telegram). Never use with real merchant data.
PUBLIC_DEMO: bool = _bool("PUBLIC_DEMO", False)
TRUST_PROXY: bool = _bool("TRUST_PROXY", False)   # behind a hosting proxy: take the client IP from X-Forwarded-For

# --- Data -------------------------------------------------------------------------
DB_PATH: Path = Path(_str("MUNIM_DB_PATH", str(ROOT_DIR / "data" / "munim.db")))
DATASET_PATH: Path = Path(_str("DATASET_PATH", str(ROOT_DIR / "data" / "merchant_transactions.json")))
SEED_DEMO_DATA: bool = _bool("SEED_DEMO_DATA", True)        # load the demo store into an empty DB on startup
SEED_MERCHANT_PHONE: str = _str("SEED_MERCHANT_PHONE", "9822000001")
SEED_MERCHANT_PIN: str = _str("SEED_MERCHANT_PIN")          # empty = random PIN printed once at seed time
DEFAULT_MERCHANT_ID: str = _str("DEFAULT_MERCHANT_ID", "sharma-gs")  # used by the local CLI
# "Today" for the agent. data = latest transaction date (synthetic data ends 2026-10-02); calendar = real date.
TODAY_MODE: str = _str("TODAY_MODE", "data").lower()
DEMO_TODAY: str = _str("DEMO_TODAY")                        # explicit override (YYYY-MM-DD)

# --- Server / security --------------------------------------------------------------
HOST: str = _str("HOST", "127.0.0.1")   # local only by default
PORT: int = int(_str("PORT", "8000"))
SESSION_HOURS: int = int(_str("SESSION_HOURS", "12"))
COOKIE_SECURE: bool = _bool("COOKIE_SECURE", False)  # set true when serving over HTTPS
LOGIN_MAX_FAILURES: int = int(_str("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCKOUT_MINUTES: int = int(_str("LOGIN_LOCKOUT_MINUTES", "15"))
ASK_RATE_PER_MINUTE: int = int(_str("ASK_RATE_PER_MINUTE", "20"))
MAX_TEXT_CHARS: int = int(_str("MAX_TEXT_CHARS", "500"))
MAX_BODY_BYTES: int = int(_str("MAX_BODY_BYTES", str(64 * 1024)))
ADMIN_TOKEN: str = _str("ADMIN_TOKEN")    # enables /api/admin/metrics when set

LOG_DIR: Path = Path(_str("LOG_DIR", str(ROOT_DIR / "logs")))
LOG_LEVEL: str = _str("LOG_LEVEL", "INFO").upper()


def telegram_configured() -> bool:
    return PUBLIC_DEMO or bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def otp_configured() -> bool:
    return bool(MESSAGECENTRAL_CUSTOMER_ID and (MESSAGECENTRAL_PASSWORD or MESSAGECENTRAL_AUTH_TOKEN))


def validate() -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors stop the server from starting."""
    errors, warnings = [], []
    if LLM_PROVIDER not in _API_KEYS:
        errors.append(f"LLM_PROVIDER must be groq, gemini or anthropic (got '{LLM_PROVIDER}')")
    else:
        env_name, key = _API_KEYS[LLM_PROVIDER]
        if not key:
            errors.append(f"{env_name} is not set in .env (needed for LLM_PROVIDER={LLM_PROVIDER})")
    if LOGIN_METHOD not in ("otp", "pin"):
        errors.append(f"LOGIN_METHOD must be otp or pin (got '{LOGIN_METHOD}')")
    elif LOGIN_METHOD == "otp" and not otp_configured() and not PUBLIC_DEMO:
        errors.append("OTP login needs MESSAGECENTRAL_CUSTOMER_ID and MESSAGECENTRAL_PASSWORD (or MESSAGECENTRAL_AUTH_TOKEN) in .env")
    if DEV_MODE and HOST not in ("127.0.0.1", "localhost", "::1"):
        errors.append("DEV_MODE skips login, so it is only allowed with HOST=127.0.0.1 (local machine)")
    elif DEV_MODE:
        warnings.append("DEV_MODE is on: login is skipped on this computer. Turn it off before showing it on a network.")
    if PUBLIC_DEMO:
        warnings.append("PUBLIC_DEMO is on: no login, simulated reminders. Only for the demo store.")
    if not telegram_configured():
        warnings.append("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set: reminders will fail until Telegram is configured")
    return errors, warnings


def mode_summary() -> dict:
    return {
        "llm": f"{LLM_PROVIDER} ({LLM_MODEL})",
        "voice": "browser speech",
        "data": "SQLite",
        "reminders": "Telegram" if telegram_configured() else "Telegram (not configured)",
    }
