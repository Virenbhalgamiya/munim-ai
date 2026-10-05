"""Real-SMS OTP login: our flow (with the Message Central test double) and the Message Central client itself."""
import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db.database import get_conn, transaction
from app.services import otp_service
from tests.conftest import DEMO_PHONE, FAKE_OTP


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def send(c, phone=DEMO_PHONE):
    return c.post("/api/otp/send", json={"phone": phone})


def verify(c, code=FAKE_OTP, phone=DEMO_PHONE):
    return c.post("/api/otp/verify", json={"phone": phone, "code": code})


# ------------------------------------------------------------------ flow
def test_auth_config_and_pin_login_disabled(client):
    assert client.get("/api/auth/config").json()["method"] == "otp"
    assert client.post("/api/login", json={"phone": DEMO_PHONE, "pin": "482913"}).status_code == 404


def test_otp_login_success_sets_session(client, otp):
    r = send(client)
    assert r.status_code == 200 and otp.sent == [DEMO_PHONE]
    r = verify(client)
    assert r.status_code == 200 and r.json()["merchant"]["phone"] == DEMO_PHONE
    assert client.get("/api/me").status_code == 200
    row = get_conn().execute("SELECT status, verification_id FROM otp_requests").fetchone()
    assert (row["status"], row["verification_id"]) == ("verified", "vid-1")


def test_code_is_never_stored(client):
    send(client)
    verify(client)
    dump = json.dumps([dict(r) for r in get_conn().execute("SELECT * FROM otp_requests")]
                      + [dict(r) for r in get_conn().execute("SELECT * FROM audit_log")])
    assert FAKE_OTP not in dump


def test_unknown_phone_gets_same_answer_but_no_sms(client, otp):
    r = send(client, "9000000009")
    assert r.status_code == 200 and r.json()["sent"] is True
    assert otp.sent == []
    assert verify(client, phone="9000000009").status_code == 401


def test_verify_without_request(client):
    r = verify(client)
    assert r.status_code == 401 and "पहले OTP" in r.json()["detail"]


def test_resend_cooldown_and_hourly_cap(client, monkeypatch, otp):
    monkeypatch.setattr(config, "OTP_RESEND_SECONDS", 60)
    assert send(client).status_code == 200
    r = send(client)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    monkeypatch.setattr(config, "OTP_RESEND_SECONDS", 0)
    for _ in range(config.OTP_MAX_SENDS_PER_10_MIN - 1):
        assert send(client).status_code == 200
    assert send(client).status_code == 429
    assert len(otp.sent) == config.OTP_MAX_SENDS_PER_10_MIN


def test_new_otp_supersedes_old_one(client):
    send(client)
    send(client)
    statuses = [r["status"] for r in get_conn().execute("SELECT status FROM otp_requests ORDER BY id")]
    assert statuses == ["expired", "pending"]


def test_expired_by_time(client):
    send(client)
    with transaction() as conn:
        conn.execute("UPDATE otp_requests SET expires_at = '2000-01-01T00:00:00+00:00'")
    r = verify(client)
    assert r.status_code == 410 and "समय-सीमा" in r.json()["detail"]


def test_expired_at_provider(client, otp):
    send(client)
    otp.expire_all = True
    assert verify(client).status_code == 410


def test_wrong_code_counts_down(client):
    send(client)
    r = verify(client, "111111")
    assert r.status_code == 401 and f"{config.OTP_MAX_ATTEMPTS - 1} कोशिश" in r.json()["detail"]


def test_provider_down_returns_503_and_nothing_saved(client, otp):
    otp.fail_send = "provider"
    r = send(client)
    assert r.status_code == 503
    assert get_conn().execute("SELECT COUNT(*) FROM otp_requests").fetchone()[0] == 0


def test_invalid_phone(client):
    assert send(client, "12345abcde").status_code == 422


def test_pin_mode_still_available_when_configured(client, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_METHOD", "pin")
    assert client.post("/api/login", json={"phone": DEMO_PHONE, "pin": "482913"}).status_code == 200
    assert send(client).status_code == 404


def test_config_requires_message_central_credentials(monkeypatch):
    monkeypatch.setattr(config, "MESSAGECENTRAL_CUSTOMER_ID", "")
    errors, _ = config.validate()
    assert any("MESSAGECENTRAL" in e for e in errors)


# ------------------------------------------------------------------ Message Central client (HTTP faked)
class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_message_central_client_send_and_verify(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(("token", params))
        return _Resp(200, {"status": 200, "token": "TOKEN-1"})

    def fake_request(method, url, params=None, headers=None, timeout=None):
        calls.append((method, url.rsplit("/", 1)[-1], dict(params), headers["authToken"]))
        if url.endswith("/send"):
            return _Resp(200, {"responseCode": 200, "data": {"verificationId": "4242", "responseCode": "200"}})
        if params["code"] == "123456":
            return _Resp(200, {"responseCode": 200, "data": {"verificationStatus": "VERIFICATION_COMPLETED"}})
        return _Resp(400, {"responseCode": 702, "message": "WRONG_OTP_PROVIDED", "data": None})

    monkeypatch.setattr(otp_service.requests, "get", fake_get)
    monkeypatch.setattr(otp_service.requests, "request", fake_request)
    svc = otp_service.MessageCentralOTP()
    assert svc.send("9876543210") == "4242"
    svc.verify("9876543210", "4242", "123456")
    with pytest.raises(otp_service.OTPError) as e:
        svc.verify("9876543210", "4242", "000000")
    assert e.value.kind == "wrong"
    token_params = calls[0][1]
    assert token_params["customerId"] == "C-TEST" and token_params["scope"] == "NEW"
    assert token_params["key"] == "dGVzdC1wYXNzd29yZA=="          # base64("test-password")
    send_call = calls[1]
    assert send_call[0] == "POST" and send_call[1] == "send" and send_call[3] == "TOKEN-1"
    assert send_call[2] == {"countryCode": "91", "customerId": "C-TEST", "flowType": "SMS",
                            "mobileNumber": "9876543210", "otpLength": "6"}


@pytest.mark.parametrize("code,kind", [(705, "expired"), (800, "limit"), (506, "pending"), (501, "config"), (999, "provider")])
def test_message_central_error_mapping(code, kind):
    assert otp_service._map_error(code, {}).kind == kind


def test_message_central_refreshes_expired_token(monkeypatch):
    tokens = iter(["OLD", "NEW"])
    monkeypatch.setattr(otp_service.requests, "get", lambda *a, **k: _Resp(200, {"token": next(tokens)}))
    seen = []

    def fake_request(method, url, params=None, headers=None, timeout=None):
        seen.append(headers["authToken"])
        if headers["authToken"] == "OLD":
            return _Resp(401, {"responseCode": 401, "message": "Unauthorized"})
        return _Resp(200, {"responseCode": 200, "data": {"verificationId": "7"}})

    monkeypatch.setattr(otp_service.requests, "request", fake_request)
    assert otp_service.MessageCentralOTP().send("9876543210") == "7"
    assert seen == ["OLD", "NEW"]


def test_network_errors_never_leak_credentials(monkeypatch):
    def boom(*a, **k):
        raise otp_service.requests.ConnectionError("https://cpaas.messagecentral.com/...key=dGVzdC1wYXNzd29yZA==")

    monkeypatch.setattr(otp_service.requests, "get", boom)
    with pytest.raises(otp_service.OTPError) as e:
        otp_service.MessageCentralOTP().send("9876543210")
    assert "dGVzdC" not in str(e.value) and "password" not in str(e.value)
