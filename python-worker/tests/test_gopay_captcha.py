from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SRC_DIR = Path(__file__).parents[1] / "gopay_runtime" / "app" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from opai.core.gopay_payment_protocol import GoPayPayment


def _payment_with_session(session):
    payment = GoPayPayment.__new__(GoPayPayment)
    payment._session = session
    payment._headers = {"User-Agent": "test"}
    payment._fingerprint_expectations = {}
    return payment


def test_midtrans_post_injects_captcha_header_only_when_supplied():
    seen = {}

    class Session:
        def post(self, _url, *, headers, **_kwargs):
            seen.update(headers)
            return SimpleNamespace(status_code=200, json=lambda: {})

    payment = _payment_with_session(Session())
    payment._midtrans_post("/snap/v3/accounts/SNAP/linking", {}, auth_snap="CLIENT", captcha_token="TOKEN")
    assert seen["X-Captcha-Token"] == "TOKEN"


def test_pay_requires_captcha_token_before_linking_request(monkeypatch):
    payment = _payment_with_session(SimpleNamespace())
    result = payment.pay(
        midtrans_url="https://app.midtrans.com/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174000",
        phone="123456",
        country_code="62",
        pin="123456",
    )
    assert result["success"] is False
    assert "验证令牌" in result["detail"]


def test_pay_accepts_captcha_token_from_environment(monkeypatch):
    monkeypatch.setenv("OPAI_MIDTRANS_CAPTCHA_TOKEN", "ENV_TOKEN")
    payment = _payment_with_session(SimpleNamespace())
    # The request is intentionally not exercised; this verifies the token
    # gate is passed before network work begins.
    payment._midtrans_post = lambda *args, **kwargs: {"status": 500, "body": {}}
    result = payment.pay(
        midtrans_url="https://app.midtrans.com/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174000",
        phone="123456",
        country_code="62",
        pin="123456",
    )
    assert result["success"] is False
    assert "linking failed" in result["detail"]
