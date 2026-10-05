"""Munim AI API server (local).   python -m app.serve   ->  http://127.0.0.1:8000

Public:   GET /  (web app)   GET /health   GET /ready   GET /api/auth/config
          POST /api/otp/send  POST /api/otp/verify   (real SMS OTP)   POST /api/login (only if LOGIN_METHOD=pin)
Merchant: POST /api/logout   GET /api/me   POST /api/ask   GET /api/conversation   GET /api/conversations   POST /api/reset   GET /api/udhaar
Admin:    GET /api/admin/metrics   (header X-Admin-Token: $ADMIN_TOKEN)
"""
from __future__ import annotations

import hmac
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth, config
from app.db import database, store
from app.db.seed import bootstrap
from app.observability import metrics, request_id_var, setup_logging
from app.ratelimit import limiter

logger = logging.getLogger("munim.api")
WEB_DIR = Path(__file__).parent / "web"
COOKIE = "munim_session"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; media-src 'self' data: blob:; frame-ancestors 'none'; "
                                "base-uri 'none'; form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


# ------------------------------------------------------------------ agent (one per process; state lives in SQLite)
_agent = None
_agent_lock = threading.Lock()


def get_agent():
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                from app.agent.agent import MunimAgent
                try:
                    _agent = MunimAgent()
                except Exception as e:
                    logger.error("AI not available: %s", e)
                    raise HTTPException(503, f"AI service not configured: {e}") from e
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    bootstrap()
    errors, warnings = config.validate()
    for w in warnings:
        logger.warning(w)
    for e in errors:
        logger.error(e)
    logger.info("startup", extra={"modes": config.mode_summary(), "db": str(config.DB_PATH)})
    yield
    logger.info("shutdown")


app = FastAPI(title="Munim AI", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


# ------------------------------------------------------------------ middleware
@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id", "")
    rid = rid if re.match(r"^[A-Za-z0-9-]{8,64}$", rid) else uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    t0 = time.perf_counter()
    try:
        length = int(request.headers.get("content-length") or 0)
        if length > config.MAX_BODY_BYTES:
            response = JSONResponse({"detail": "Request too large"}, status_code=413)
        else:
            response = await call_next(request)
    except Exception:
        logger.exception("unhandled error", extra={"path": request.url.path})
        metrics.inc("http_500")
        response = JSONResponse({"detail": "Internal error", "request_id": rid}, status_code=500)
    finally:
        request_id_var.reset(token)
    elapsed = time.perf_counter() - t0
    response.headers["X-Request-ID"] = rid
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        response.headers.setdefault("Cache-Control", "no-cache")   # always revalidate page/JS/CSS after updates
    metrics.inc("http_requests")
    metrics.inc(f"http_{response.status_code // 100}xx")
    metrics.observe("http_request", elapsed)
    logger.info("http", extra={"method": request.method, "path": request.url.path, "status": response.status_code,
                               "ms": round(elapsed * 1000), "request_id": rid})
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "Invalid input", "errors": [e.get("msg") for e in exc.errors()][:5]}, status_code=422)


# ------------------------------------------------------------------ auth helpers
def _client_ip(request: Request) -> str:
    if config.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "unknown"


def _limit_key(m: dict, request: Request) -> str:
    """Rate-limit bucket: per merchant; in the public demo everyone shares one shop, so per visitor."""
    return f"{m['id']}:{_client_ip(request)}" if config.PUBLIC_DEMO else m["id"]


def _open_access() -> bool:
    """No-login access: public demo, or developer mode on this computer."""
    return config.PUBLIC_DEMO or config.DEV_MODE


def _token(request: Request) -> str | None:
    if request.cookies.get(COOKIE):
        return request.cookies[COOKIE]
    h = request.headers.get("authorization", "")
    return h[7:] if h.lower().startswith("bearer ") else None


def current_merchant(request: Request) -> dict:
    m = auth.merchant_for_token(_token(request))
    if not m:
        raise HTTPException(401, "Login required")
    return m


def _check_session_id(session_id: str) -> str:
    if not SESSION_ID_RE.match(session_id or ""):
        raise HTTPException(422, "Invalid session_id")
    return session_id


# ------------------------------------------------------------------ public
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


APP_VERSION = "1.6.0"   # keep in sync with APP_VERSION in web/static/app.js


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": APP_VERSION}


@app.get("/ready")
def ready() -> JSONResponse:
    errors, warnings = config.validate()
    checks = {"database": database.healthcheck(), "ai_configured": not errors,
              "telegram_configured": config.telegram_configured()}
    ok = checks["database"] and checks["ai_configured"]   # Telegram missing = reminders off, app still usable
    return JSONResponse({"ok": ok, "checks": checks, "errors": errors, "warnings": warnings,
                         "modes": config.mode_summary()}, status_code=200 if ok else 503)


class LoginIn(BaseModel):
    phone: str = Field(min_length=10, max_length=15)
    pin: str = Field(min_length=4, max_length=6)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=config.SESSION_HOURS * 3600, httponly=True, samesite="strict",
                        secure=config.COOKIE_SECURE, path="/")


@app.get("/api/auth/config")
def auth_config() -> dict:
    return {"method": config.LOGIN_METHOD, "otp_length": 6, "resend_seconds": config.OTP_RESEND_SECONDS,
            "dev_mode": config.DEV_MODE or config.PUBLIC_DEMO, "public_demo": config.PUBLIC_DEMO,
            "voice_engine": config.VOICE_ENGINE, "version": APP_VERSION}


_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


@app.post("/api/dev/login")
def dev_login(request: Request, response: Response) -> dict:
    """Developer mode: log in as the default merchant without phone/OTP. Local machine only."""
    if not _open_access():
        raise HTTPException(404, "Not found")
    ip = _client_ip(request)
    if not config.PUBLIC_DEMO and ip not in _LOOPBACK:
        raise HTTPException(403, "Developer mode works only on this computer")
    try:
        token, merchant = auth.dev_login(config.DEFAULT_MERCHANT_ID, ip)
    except auth.AuthError as e:
        raise HTTPException(404, str(e)) from e
    _set_session_cookie(response, token)
    return {"ok": True, "merchant": store.public_profile(merchant), "dev_mode": True}


class OtpSendIn(BaseModel):
    phone: str = Field(min_length=10, max_length=15)


class OtpVerifyIn(BaseModel):
    phone: str = Field(min_length=10, max_length=15)
    code: str = Field(min_length=4, max_length=8)


@app.post("/api/otp/send")
def otp_send(body: OtpSendIn, request: Request) -> dict:
    from app.services.otp_service import OTPError

    if config.LOGIN_METHOD != "otp":
        raise HTTPException(404, "Not found")
    ip = _client_ip(request)
    ok, retry = limiter.allow(f"otp-send:{ip}", 10, 3600)
    if not ok:
        raise HTTPException(429, "बहुत बार OTP माँगा गया, बाद में कोशिश कीजिए", headers={"Retry-After": str(int(retry) + 1)})
    try:
        result = auth.request_otp(body.phone, ip)
    except auth.TooSoon as e:
        raise HTTPException(429, str(e), headers={"Retry-After": str(e.wait_seconds)}) from e
    except auth.LockedOut as e:
        raise HTTPException(423, str(e)) from e
    except auth.AuthError as e:
        raise HTTPException(422, str(e)) from e
    except OTPError as e:
        metrics.inc("otp_provider_errors")
        raise HTTPException(503 if e.kind in ("config", "provider") else 429, str(e)) from e
    metrics.inc("otp_sent")
    return result


@app.post("/api/otp/verify")
def otp_verify(body: OtpVerifyIn, request: Request, response: Response) -> dict:
    from app.services.otp_service import OTPError

    if config.LOGIN_METHOD != "otp":
        raise HTTPException(404, "Not found")
    ip = _client_ip(request)
    ok, retry = limiter.allow(f"otp-verify:{ip}", 20, 600)
    if not ok:
        raise HTTPException(429, "बहुत कोशिशें हुईं, थोड़ी देर बाद कोशिश कीजिए", headers={"Retry-After": str(int(retry) + 1)})
    try:
        token, merchant = auth.verify_otp(body.phone, body.code, ip)
    except auth.LockedOut as e:
        raise HTTPException(423, str(e)) from e
    except auth.OTPExpired as e:
        raise HTTPException(410, str(e)) from e
    except auth.AuthError as e:
        metrics.inc("login_failed")
        raise HTTPException(401, str(e)) from e
    except OTPError as e:
        metrics.inc("otp_provider_errors")
        raise HTTPException(503, str(e)) from e
    metrics.inc("login_ok")
    _set_session_cookie(response, token)
    return {"ok": True, "merchant": store.public_profile(merchant)}


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    if config.LOGIN_METHOD != "pin":
        raise HTTPException(404, "PIN login is disabled; use OTP")
    ip = _client_ip(request)
    ok, retry = limiter.allow(f"login:{ip}", 10, 60)
    if not ok:
        raise HTTPException(429, f"Too many attempts. Retry in {retry}s", headers={"Retry-After": str(int(retry) + 1)})
    try:
        token, merchant = auth.login(body.phone, body.pin, ip)
    except auth.LockedOut as e:
        raise HTTPException(423, str(e)) from e
    except auth.AuthError as e:
        metrics.inc("login_failed")
        raise HTTPException(401, str(e)) from e
    metrics.inc("login_ok")
    _set_session_cookie(response, token)
    return {"ok": True, "merchant": store.public_profile(merchant)}


# ------------------------------------------------------------------ merchant
@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    auth.logout(_token(request))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/session")
def session_status(request: Request) -> dict:
    """Boot check for the web page: 200 either way (a 401 here would just be console noise)."""
    m = auth.merchant_for_token(_token(request))
    if not m:
        return {"authenticated": False}
    return {"authenticated": True, "merchant": store.public_profile(m), "modes": config.mode_summary()}


@app.get("/api/me")
def me(m: dict = Depends(current_merchant)) -> dict:
    return {"merchant": store.public_profile(m), "modes": config.mode_summary()}


class AskIn(BaseModel):
    text: str = Field(min_length=1)
    session_id: str = "default"
    lang: str = Field(default="hi", pattern="^(hi|mr|en)$")   # reply language: Hindi (Devanagari) / Marathi / English


@app.post("/api/ask")
def ask(body: AskIn, request: Request, m: dict = Depends(current_merchant)) -> dict:
    """The web page turns speech into text in the browser and sends the text here."""
    _check_session_id(body.session_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty question")
    if len(text) > config.MAX_TEXT_CHARS:
        raise HTTPException(422, f"Question too long (max {config.MAX_TEXT_CHARS} characters)")
    ok, retry = limiter.allow(f"ask:{_limit_key(m, request)}", config.ASK_RATE_PER_MINUTE, 60)
    if not ok:
        metrics.inc("rate_limited")
        raise HTTPException(429, f"थोड़ा रुककर पूछिए ({retry} सेकंड)", headers={"Retry-After": str(int(retry) + 1)})
    result = get_agent().ask(text, session_id=body.session_id, merchant_id=m["id"], lang=body.lang)
    return result.to_dict()


@app.get("/api/conversation")
def conversation(session_id: str = "default", m: dict = Depends(current_merchant)) -> dict:
    _check_session_id(session_id)
    return {"session_id": session_id, "messages": store.conversation_transcript(m["id"], session_id)}


@app.get("/api/conversations")
def conversations(m: dict = Depends(current_merchant)) -> dict:
    """Past chats, so "new chat" never loses the old ones."""
    return {"conversations": store.list_conversations(m["id"])}


class ResetIn(BaseModel):
    session_id: str = "default"


@app.post("/api/reset")
def reset(body: ResetIn, m: dict = Depends(current_merchant)) -> dict:
    store.reset_session(m["id"], _check_session_id(body.session_id))
    return {"ok": True, "session_id": body.session_id}


@app.get("/api/dashboard")
def dashboard(lang: str = "hi", m: dict = Depends(current_merchant)) -> dict:
    """KPIs, charts, ranked insights and the spoken briefing (hi / en / mr), computed from data (no AI call)."""
    from app.agent.insights import build_dashboard
    return build_dashboard(m["id"], lang)


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    lang: str = Field(default="hi", pattern="^(hi|mr|en)$")
    gender: str = Field(default="female", pattern="^(female|male)$")


@app.post("/api/tts")
async def tts(body: TtsIn, request: Request, m: dict = Depends(current_merchant)):
    """Neural Hindi/Marathi/English speech (MP3). The page falls back to the browser voice on 503."""
    from app.services.tts_service import TTSError, synthesize

    if config.VOICE_ENGINE != "neural":
        raise HTTPException(503, "Neural voice disabled")
    ok, retry = limiter.allow(f"tts:{_limit_key(m, request)}", 40, 60)
    if not ok:
        raise HTTPException(429, "थोड़ा रुकिए", headers={"Retry-After": str(int(retry) + 1)})
    try:
        audio = await synthesize(body.text, body.lang, body.gender)
    except TTSError as e:
        metrics.inc("tts_failures")
        logger.warning("tts failed: %s", e)
        raise HTTPException(503, "Neural voice unavailable") from e
    metrics.inc("tts_ok")
    return RawResponse(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})


# ------------------------------------------------------------------ incoming UPI payments -> udhaar repayment
@app.get("/api/payments/pending")
def payments_pending(m: dict = Depends(current_merchant)) -> dict:
    from app import payments
    return {"payments": payments.list_pending(m["id"])}


def _decide(m: dict, payment_id: int, action: str) -> dict:
    from app import payments
    try:
        if action == "link":
            out = payments.link_as_repayment(m["id"], payment_id)
        else:
            out = payments.dismiss_as_purchase(m["id"], payment_id)
    except payments.PaymentError as e:
        raise HTTPException(409 if "already" in str(e) else 404, str(e)) from e
    metrics.inc(f"payment_{action}")
    return {"ok": True, "payment": out}


@app.post("/api/payments/{payment_id}/link")
def payment_link(payment_id: int, m: dict = Depends(current_merchant)) -> dict:
    """Merchant: yes, this UPI payment repays udhaar."""
    return _decide(m, payment_id, "link")


@app.post("/api/payments/{payment_id}/dismiss")
def payment_dismiss(payment_id: int, m: dict = Depends(current_merchant)) -> dict:
    """Merchant: no, it was a purchase (recorded as a sale)."""
    return _decide(m, payment_id, "dismiss")


class UpiWebhookIn(BaseModel):
    merchant_id: str = Field(min_length=3, max_length=40)
    txn_id: str = Field(min_length=1, max_length=80)
    amount: int = Field(gt=0, le=1_000_000)
    payer_vpa: str = Field(default="", max_length=120)
    payer_name: str = Field(default="", max_length=120)


@app.post("/api/webhooks/upi")
def upi_webhook(body: UpiWebhookIn, x_webhook_secret: str | None = Header(default=None)) -> dict:
    """Production entry point: the payment provider notifies a non-sale UPI payment. Idempotent on txn_id."""
    from app import payments
    if not config.UPI_WEBHOOK_SECRET:
        raise HTTPException(404, "Not found")
    if not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, config.UPI_WEBHOOK_SECRET):
        raise HTTPException(403, "Forbidden")
    if not store.get_merchant(body.merchant_id):
        raise HTTPException(404, "Unknown merchant")
    p = payments.record_incoming(body.merchant_id, body.amount, body.payer_vpa, body.payer_name, body.txn_id, "webhook")
    return {"ok": True, "status": p["status"], "payment_id": p["id"], "duplicate": p.get("duplicate", False)}


class SimulateUpiIn(BaseModel):
    customer: str = Field(min_length=1, max_length=60)
    amount: int = Field(gt=0, le=100_000)


@app.post("/api/dev/simulate-upi")
def simulate_upi(body: SimulateUpiIn, request: Request, m: dict = Depends(current_merchant)) -> dict:
    """Developer mode demo: pretend a customer just sent money by UPI (same code path as the webhook)."""
    from app import payments
    if not _open_access():
        raise HTTPException(404, "Not found")
    if not config.PUBLIC_DEMO and _client_ip(request) not in _LOOPBACK:
        raise HTTPException(403, "Developer mode works only on this computer")
    customers = store.list_customers(m["id"])
    from app.agent.tools import match_customer
    found = match_customer(body.customer, customers, key="name")
    vpa = found[0].get("upi_vpa") if len(found) == 1 else ""
    p = payments.record_incoming(m["id"], body.amount, vpa or "", body.customer, None, "demo")
    return {"ok": True, "payment": p}


@app.get("/api/udhaar")
def udhaar(m: dict = Depends(current_merchant)) -> dict:
    from app.agent.tools import udhaar_lookup
    with store.merchant_scope(m["id"]):
        return udhaar_lookup()


# ------------------------------------------------------------------ admin
@app.get("/api/admin/metrics")
def admin_metrics(x_admin_token: str | None = Header(default=None)) -> dict:
    if not config.ADMIN_TOKEN:
        raise HTTPException(404, "Not found")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, config.ADMIN_TOKEN):
        raise HTTPException(403, "Forbidden")
    return metrics.snapshot()
