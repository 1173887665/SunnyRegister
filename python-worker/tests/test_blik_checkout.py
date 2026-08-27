from __future__ import annotations

import sys
from pathlib import Path


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402
from provider_checkout import (  # noqa: E402
    PROVIDER_DEFAULTS,
    blik_hosted_payment_url,
    default_billing,
    is_valid_blik_hosted_payment_url,
)


def test_blik_defaults_and_polish_billing() -> None:
    assert PROVIDER_DEFAULTS["blik"] == {"country": "PL", "currency": "PLN"}
    billing = default_billing("PL", "user@example.com")
    assert billing["email"] == "user@example.com"
    assert billing["address"]["country"] == "PL"
    assert billing["address"]["city"] == "Warszawa"
    assert billing["address"]["postal_code"] == "00-001"


def test_blik_hosted_url_preserves_signed_stripe_url_exactly() -> None:
    session_id = "cs_live_a1Dun8t2Mnbx5xOiuPgV114UYeOts1740P86fVFrQGWQw1CrjivvM88fH5"
    source = f"https://checkout.stripe.com/c/pay/{session_id}#fidnandhYHdWcXxpYCc%2FJ2FgY2RwaXEn"
    result = blik_hosted_payment_url(source, session_id)
    assert is_valid_blik_hosted_payment_url(result, session_id)
    assert result == source


def test_blik_hosted_url_rejects_generic_or_wrong_session_pages() -> None:
    assert not is_valid_blik_hosted_payment_url(
        "https://chatgpt.com/checkout/openai_ie/cs_live_123", "cs_live_123",
    )
    assert not is_valid_blik_hosted_payment_url(
        "https://pay.openai.com/c/pay/cs_live_other", "cs_live_123",
    )
    assert not is_valid_blik_hosted_payment_url(
        "https://pay.openai.com.evil.example/c/pay/cs_live_123", "cs_live_123",
    )


def test_blik_hosted_url_does_not_fabricate_missing_provider_url() -> None:
    assert blik_hosted_payment_url("", "cs_live_123") == ""


def test_blik_hosted_url_rejects_legacy_synthetic_query_parameters() -> None:
    broken = (
        "https://checkout.stripe.com/c/pay/cs_live_123"
        "?redirect_pm_type=blik&lid=generated&ui_mode=custom#checkout-state"
    )
    assert not is_valid_blik_hosted_payment_url(broken, "cs_live_123")
    assert blik_hosted_payment_url(broken, "cs_live_123") == ""


def test_oaics_blik_url_requires_matching_session() -> None:
    valid = "https://chatgpt.com/checkout/openai_ie/oaics_123456"
    assert checkout_app.is_valid_oaics_blik_payment_url(valid, "oaics_123456")
    assert not checkout_app.is_valid_oaics_blik_payment_url(valid, "oaics_other")
    assert not checkout_app.is_valid_oaics_blik_payment_url(
        "https://chatgpt.com.evil.example/checkout/openai_ie/oaics_123456", "oaics_123456",
    )


def test_blik_checkout_payload_applies_promo_after_method_detection() -> None:
    options = {
        "plan": "plus",
        "link_type": "blik",
        "country": "PL",
        "currency": "PLN",
        "checkout_country": "PL",
        "checkout_currency": "PLN",
        "use_promo": True,
        "promo_campaign": "plus-1-month-free",
    }
    payload = checkout_app.checkout_payload(options, {})
    assert payload["billing_details"] == {"country": "PL", "currency": "PLN"}
    assert payload["checkout_ui_mode"] == "custom"
    assert "promo_campaign" not in payload


def test_oaics_blik_method_selection_is_provider_specific() -> None:
    payload = {
        "custom_payment_methods": [
            {"id": "cpmt_p24", "name": "Przelewy24"},
            {"id": "cpmt_blik", "name": "BLIK"},
        ],
    }
    methods = checkout_app.custom_payment_methods_for(payload, "blik")
    assert [item["id"] for item in methods] == ["cpmt_blik"]
