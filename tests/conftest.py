"""Test setup.

- The app has no mocks. For the offline suite, the AI is replaced by tests/fake_llm.py (a test double)
  and Telegram by a recorder, so tests never hit the network, never use the free Groq quota and never
  send a real message. MUNIM_TEST_REAL=1 uses the real AI from .env (Telegram is still recorded).
- Every test gets its own copy of a freshly seeded SQLite database (your data/munim.db is never touched).
"""
import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="munim-tests-"))
REAL = bool(os.getenv("MUNIM_TEST_REAL"))
# Set explicitly (not just pop): load_dotenv never overrides existing env vars, so a real .env can't leak in.
os.environ.update({
    "MUNIM_DB_PATH": str(_TMP / "template.db"), "LOG_DIR": str(_TMP / "logs"),
    "SEED_MERCHANT_PIN": "482913", "SEED_MERCHANT_PHONE": "9822000001", "ADMIN_TOKEN": "test-admin-token", "DEMO_TODAY": "", "TODAY_MODE": "data",
    "TELEGRAM_BOT_TOKEN": "test-bot-token", "TELEGRAM_CHAT_ID": "1000001",
    "LOGIN_METHOD": "otp", "MESSAGECENTRAL_CUSTOMER_ID": "C-TEST", "MESSAGECENTRAL_PASSWORD": "test-password",
    "MESSAGECENTRAL_AUTH_TOKEN": "", "DEV_MODE": "false", "UPI_WEBHOOK_SECRET": "", "VOICE_ENGINE": "neural",
    "HOST": "127.0.0.1", "SEED_DEMO_DATA": "true",
})
if not REAL:
    os.environ.update({"LLM_PROVIDER": "groq", "GROQ_API_KEY": "test-key-not-used"})

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.agent.agent import MunimAgent  # noqa: E402
from app.db import database, store  # noqa: E402
from app.db.seed import seed_demo  # noqa: E402
from app.ratelimit import limiter  # noqa: E402

DEMO_PHONE, DEMO_PIN, DEMO_MERCHANT = "9822000001", "482913", "sharma-gs"
FAKE_OTP = "246810"


@pytest.fixture(scope="session")
def template_db() -> Path:
    path = _TMP / "template.db"
    config.DB_PATH = path
    seed_demo(pin=DEMO_PIN)
    from app.db.seed import seed_demo_repayment
    seed_demo_repayment()          # same as real startup: one pending UPI repayment suggestion (Rajesh ₹1,000)
    database.get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database.close_conn()
    return path


@pytest.fixture(autouse=True)
def fresh_db(template_db, tmp_path, monkeypatch):
    db = tmp_path / "munim.db"
    shutil.copy(template_db, db)
    monkeypatch.setattr(config, "DB_PATH", db)
    database.close_conn()
    store.invalidate_cache()
    limiter.reset()
    with store.merchant_scope(DEMO_MERCHANT):
        yield db
    database.close_conn()


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    """Offline suite: the agent talks to the test double instead of Groq."""
    if REAL:
        return
    from tests.fake_llm import FakeLLM
    monkeypatch.setattr("app.agent.agent.get_llm_service", lambda: FakeLLM())


class _TelegramRecorder:
    def __init__(self):
        self.calls = []

    def send_message(self, text, chat_id=None):
        self.calls.append({"text": text, "chat_id": chat_id})
        return {"ok": True, "chat_id": chat_id, "message_id": len(self.calls)}


@pytest.fixture(autouse=True)
def telegram(monkeypatch):
    """Every test: Telegram messages are recorded, never sent."""
    rec = _TelegramRecorder()
    monkeypatch.setattr("app.services.telegram_service.get_telegram_service", lambda: rec)
    return rec.calls


class _OtpRecorder:
    """Stands in for Message Central in tests: no real SMS, fixed code FAKE_OTP."""

    def __init__(self):
        self.sent, self.fail_send, self.expire_all = [], None, False

    def send(self, phone):
        from app.services.otp_service import OTPError
        if self.fail_send:
            raise OTPError(self.fail_send, "OTP service error (test)")
        self.sent.append(phone)
        return f"vid-{len(self.sent)}"

    def verify(self, phone, verification_id, code):
        from app.services.otp_service import OTPError
        if self.expire_all:
            raise OTPError("expired", "OTP की समय-सीमा खत्म हो गई, नया OTP मँगाइए")
        if code != FAKE_OTP:
            raise OTPError("wrong", "OTP गलत है")


@pytest.fixture(autouse=True)
def otp(monkeypatch):
    rec = _OtpRecorder()
    monkeypatch.setattr("app.services.otp_service.get_otp_service", lambda: rec)
    monkeypatch.setattr(config, "OTP_RESEND_SECONDS", 0)
    return rec


@pytest.fixture
def agent():
    return MunimAgent()
