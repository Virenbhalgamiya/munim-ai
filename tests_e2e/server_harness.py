"""Runs the REAL Munim AI server (uvicorn, real HTTP, real SQLite, real neural TTS) for functional tests.

Only the two outside services that would contact real people are replaced, inside this test process:
  * Message Central OTP  -> accepts code 246810, never sends an SMS
  * Telegram             -> appends each message to a JSONL file instead of delivering it
and, unless --real-ai is given, the AI is the deterministic test brain (tests/fake_llm.py) so runs are
repeatable and don't use the free Groq quota.

  python tests_e2e/server_harness.py --port 8790 --db <path> [--dev-mode] [--real-ai] [--tg-log <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE_OTP = "246810"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--tg-log", required=True)
    ap.add_argument("--dev-mode", action="store_true")
    ap.add_argument("--real-ai", action="store_true")
    ap.add_argument("--ask-rate", default="20")
    ap.add_argument("--webhook-secret", default="")
    args = ap.parse_args()

    os.environ.update({
        "MUNIM_DB_PATH": args.db, "LOG_DIR": str(Path(args.db).parent / "logs"),
        "DEV_MODE": "true" if args.dev_mode else "false", "HOST": "127.0.0.1",
        "SEED_MERCHANT_PHONE": "9822000001", "SEED_MERCHANT_PIN": "482913",
        "LOGIN_METHOD": "otp", "MESSAGECENTRAL_CUSTOMER_ID": "C-E2E", "MESSAGECENTRAL_PASSWORD": "e2e",
        "TELEGRAM_BOT_TOKEN": "e2e-token", "TELEGRAM_CHAT_ID": "1000001",
        "ASK_RATE_PER_MINUTE": args.ask_rate, "OTP_RESEND_SECONDS": "2", "ADMIN_TOKEN": "e2e-admin",
        "UPI_WEBHOOK_SECRET": args.webhook_secret, "VOICE_ENGINE": "neural", "SEED_DEMO_DATA": "true",
    })
    if not args.real_ai:
        os.environ.update({"LLM_PROVIDER": "groq", "GROQ_API_KEY": "not-used-by-test-brain"})

    import uvicorn

    from app.services import otp_service, telegram_service
    from app.services.otp_service import OTPError

    class OtpRecorder:
        def send(self, phone):
            with open(args.tg_log + ".otp", "a", encoding="utf-8") as f:
                f.write(phone + "\n")
            return "vid-e2e"

        def verify(self, phone, verification_id, code):
            if code != FAKE_OTP:
                raise OTPError("wrong", "OTP गलत है")

    class TelegramRecorder:
        def send_message(self, text, chat_id=None):
            with open(args.tg_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"chat_id": chat_id, "text": text}, ensure_ascii=False) + "\n")
            return {"ok": True, "chat_id": chat_id, "message_id": 1}

    otp_service.get_otp_service = lambda: OtpRecorder()
    telegram_service.get_telegram_service = lambda: TelegramRecorder()
    if not args.real_ai:
        from app.agent import agent as agent_module
        from tests.fake_llm import FakeLLM
        agent_module.get_llm_service = lambda: FakeLLM()

    from app.db.seed import bootstrap
    from app.main import app
    bootstrap(verbose=False)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
