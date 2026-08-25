from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402
from provider_checkout import PROVIDER_DEFAULTS, default_billing  # noqa: E402


MIDTRANS_V3 = "https://app.midtrans.com/snap/v3/redirection/123e4567-e89b-12d3-a456-426614174000"
MIDTRANS_V4 = "https://app.midtrans.com/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174001"


def test_gopay_midtrans_url_accepts_reference_snap_versions() -> None:
    assert checkout_app.is_valid_gopay_midtrans_url(MIDTRANS_V3)
    assert checkout_app.is_valid_gopay_midtrans_url(MIDTRANS_V4 + "?source=chatgpt")


def test_gopay_midtrans_url_rejects_non_provider_and_lookalike_urls() -> None:
    assert not checkout_app.is_valid_gopay_midtrans_url(
        "https://app.midtrans.com.evil.example/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174001"
    )
    assert not checkout_app.is_valid_gopay_midtrans_url(
        "https://app.midtrans.com/snap/v4/redirection/not-a-uuid"
    )
    assert not checkout_app.is_valid_gopay_midtrans_url(
        "https://chatgpt.com/checkout/openai_ie/oaics_example"
    )


def test_gopay_midtrans_url_finds_nested_and_encoded_handoff() -> None:
    payload = {
        "next_action": {"url": "https://chatgpt.com/checkout/verify"},
        "provider_data": {"redirect_url": quote(MIDTRANS_V4, safe="")},
    }
    assert checkout_app.gopay_midtrans_url(payload) == MIDTRANS_V4


def test_gopay_method_selection_prefers_gopay_cpmt() -> None:
    payload = {
        "custom_payment_methods": [
            {"id": "cpmt_paypal", "name": "PayPal"},
            {"id": "cpmt_gopay", "name": "GoPay wallet"},
        ],
    }
    assert checkout_app.custom_payment_method_id_for(payload, "gopay") == "cpmt_gopay"


def test_gopay_defaults_use_indonesia_billing() -> None:
    assert PROVIDER_DEFAULTS["gopay"] == {"country": "ID", "currency": "IDR"}
    billing = default_billing("ID", "user@example.com")
    assert billing["email"] == "user@example.com"
    assert billing["address"]["country"] == "ID"
    assert billing["address"]["city"] == "Jakarta"
    assert billing["address"]["postal_code"] == "10310"
