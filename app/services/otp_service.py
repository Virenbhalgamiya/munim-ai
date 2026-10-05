"""Real SMS OTP via Message Central VerifyNow (https://cpaas.messagecentral.com).

API (from Message Central's docs):
  GET  /auth/v1/authentication/token?customerId=&key=<base64 password>&scope=NEW[&country=IN&email=]  -> {"token"}
  POST /verification/v3/send?countryCode=91&customerId=&flowType=SMS&mobileNumber=&otpLength=6     -> data.verificationId
  GET  /verification/v3/validateOtp?verificationId=&code=[&countryCode&mobileNumber&customerId]     -> data.verificationStatus
  Header: authToken. Error responseCodes: 702 wrong OTP, 705 expired, 703 already verified,
  800 max limit reached, 506 request already exists, 401 bad token.

Message Central generates, delivers and checks the code; we only keep the verificationId.
The auth token and OTP codes are never logged.
"""
from __future__ import annotations

import base64
import logging
import threading

import requests

from app import config

logger = logging.getLogger(__name__)
BASE_URL = "https://cpaas.messagecentral.com"


class OTPError(Exception):
    """kind: wrong | expired | limit | pending | config | provider"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class MessageCentralOTP:
    def __init__(self) -> None:
        self.customer_id = config.MESSAGECENTRAL_CUSTOMER_ID
        if not self.customer_id or not (config.MESSAGECENTRAL_AUTH_TOKEN or config.MESSAGECENTRAL_PASSWORD):
            raise OTPError("config", "OTP सेवा सेट नहीं है (.env में MESSAGECENTRAL_* नहीं है)")
        self._token = config.MESSAGECENTRAL_AUTH_TOKEN or None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- auth token
    def _fetch_token(self) -> str:
        params = {"customerId": self.customer_id, "scope": "NEW", "country": "IN",
                  "key": base64.b64encode(config.MESSAGECENTRAL_PASSWORD.encode()).decode()}
        if config.MESSAGECENTRAL_EMAIL:
            params["email"] = config.MESSAGECENTRAL_EMAIL
        try:
            resp = requests.get(f"{BASE_URL}/auth/v1/authentication/token", params=params, timeout=15)
        except requests.RequestException as e:
            raise OTPError("provider", f"OTP सेवा से जुड़ नहीं पाया ({type(e).__name__})") from None
        token = (_json(resp) or {}).get("token")
        if resp.status_code != 200 or not token:
            logger.error("Message Central token request failed: HTTP %s", resp.status_code)
            raise OTPError("config", "OTP सेवा में लॉगिन नहीं हुआ: customer ID / password जाँचिए")
        return token

    def _auth(self, refresh: bool = False) -> str:
        with self._lock:
            if refresh or not self._token:
                if not config.MESSAGECENTRAL_PASSWORD:
                    raise OTPError("config", "Message Central का auth token गलत है (MESSAGECENTRAL_AUTH_TOKEN)")
                self._token = self._fetch_token()
            return self._token

    def _call(self, method: str, path: str, params: dict) -> dict:
        for attempt in range(2):
            try:
                resp = requests.request(method, f"{BASE_URL}{path}", params=params,
                                        headers={"authToken": self._auth(refresh=attempt == 1), "Accept": "*/*"},
                                        timeout=15)
            except requests.RequestException as e:
                raise OTPError("provider", f"OTP सेवा से जुड़ नहीं पाया ({type(e).__name__})") from None
            body = _json(resp) or {}
            code = _response_code(body, resp.status_code)
            if code == 401 and attempt == 0 and config.MESSAGECENTRAL_PASSWORD:
                continue  # token expired -> refresh once
            return {"code": code, "body": body}
        return {"code": 401, "body": {}}

    # ---------------------------------------------------------------- API
    def send(self, phone: str) -> str:
        """Sends a 6-digit OTP SMS to +91<phone>. Returns Message Central's verificationId."""
        r = self._call("POST", "/verification/v3/send", {
            "countryCode": "91", "customerId": self.customer_id, "flowType": "SMS",
            "mobileNumber": phone, "otpLength": "6"})
        vid = (r["body"].get("data") or {}).get("verificationId")
        if r["code"] == 200 and vid:
            return str(vid)
        raise _map_error(r["code"], r["body"])

    def verify(self, phone: str, verification_id: str, code: str) -> None:
        """Raises OTPError unless Message Central confirms the code."""
        r = self._call("GET", "/verification/v3/validateOtp", {
            "countryCode": "91", "mobileNumber": phone, "verificationId": verification_id,
            "customerId": self.customer_id, "code": code})
        status = (r["body"].get("data") or {}).get("verificationStatus")
        if r["code"] == 200 and status == "VERIFICATION_COMPLETED":
            return
        raise _map_error(r["code"], r["body"])


def _json(resp) -> dict | None:
    try:
        return resp.json()
    except ValueError:
        return None


def _response_code(body: dict, http_status: int) -> int:
    for v in ((body.get("data") or {}).get("responseCode"), body.get("responseCode")):
        try:
            if v is not None and int(v) != 200:
                return int(v)
        except (TypeError, ValueError):
            pass
    try:
        return int(body.get("responseCode", http_status))
    except (TypeError, ValueError):
        return http_status


def _map_error(code: int, body: dict) -> OTPError:
    msg = (body.get("data") or {}).get("errorMessage") or body.get("message") or ""
    logger.warning("Message Central error %s: %s", code, msg)
    if code == 702:
        return OTPError("wrong", "OTP गलत है")
    if code in (705, 703):
        return OTPError("expired", "OTP की समय-सीमा खत्म हो गई, नया OTP मँगाइए")
    if code == 800:
        return OTPError("limit", "OTP की सीमा पूरी हो गई, थोड़ी देर बाद कोशिश कीजिए")
    if code == 506:
        return OTPError("pending", "OTP पहले ही भेजा जा चुका है, थोड़ा रुककर दोबारा मँगाइए")
    if code in (401, 501):
        return OTPError("config", "OTP सेवा का customer ID / token गलत है")
    if code == 511:
        return OTPError("provider", "फ़ोन नंबर का देश-कोड गलत है")
    return OTPError("provider", f"OTP service error ({code})")


_service: MessageCentralOTP | None = None
_service_lock = threading.Lock()


def get_otp_service() -> MessageCentralOTP:
    global _service
    with _service_lock:
        if _service is None:
            _service = MessageCentralOTP()
        return _service
