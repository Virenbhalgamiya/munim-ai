"""Telegram Bot API sendMessage: delivers udhaar reminders."""
from __future__ import annotations

import requests

from app import config


class TelegramNotConfigured(RuntimeError):
    pass


class TelegramService:
    def __init__(self, bot_token: str | None = None, default_chat_id: str | None = None) -> None:
        self.bot_token = bot_token or config.TELEGRAM_BOT_TOKEN
        self.default_chat_id = default_chat_id or config.TELEGRAM_CHAT_ID
        if not self.bot_token:
            raise TelegramNotConfigured("Telegram सेट नहीं है (TELEGRAM_BOT_TOKEN नहीं है)")

    def send_message(self, text: str, chat_id: str | None = None) -> dict:
        chat = chat_id or self.default_chat_id
        if not chat:
            raise TelegramNotConfigured("Telegram chat id सेट नहीं है (TELEGRAM_CHAT_ID नहीं है)")
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": chat, "text": text},
                timeout=10,
            )
        except requests.RequestException as e:
            # requests' error text contains the URL (and so the bot token): never pass it on.
            raise RuntimeError(f"Telegram से जुड़ नहीं पाया ({type(e).__name__})") from None
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code != 200 or not body.get("ok"):
            # Telegram explains failures in "description" (e.g. "chat not found"); never echo the token.
            raise RuntimeError(f"Telegram error {resp.status_code}: {body.get('description', 'unknown error')}")
        return {"ok": True, "chat_id": chat, "message_id": body.get("result", {}).get("message_id")}


class DemoTelegramService:
    """Public demo: messages are not sent anywhere (visitors must not be able to message a real phone)."""

    demo = True

    def send_message(self, text: str, chat_id: str | None = None) -> dict:
        return {"message_id": None, "demo": True}


def get_telegram_service() -> TelegramService:
    from app import config as _cfg
    if _cfg.PUBLIC_DEMO:
        return DemoTelegramService()  # type: ignore[return-value]
    return TelegramService()
