from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402


def test_gcash_authorization_url_requires_m_gcash_host() -> None:
    assert checkout_app.is_valid_gcash_authorization_url(
        "https://m.gcash.com/gcashapp/gcash-merchants-auth/index.html?siteId=1"
    ) is True
    assert checkout_app.is_valid_gcash_authorization_url(
        "https://checkoutshopper-live.adyen.com/checkoutshopper/checkoutPaymentRedirect?redirectData=x"
    ) is False
    assert checkout_app.is_valid_gcash_authorization_url(
        "https://gcash.com/gcashapp/authorization"
    ) is False


def test_gcash_authorization_url_prefers_nested_final_link() -> None:
    payload = {
        "next_action": {
            "url": "https://checkoutshopper-live.adyen.com/checkoutshopper/checkoutPaymentRedirect?redirectData=x",
        },
        "confirm_return_url": "https://m.gcash.com/gcashapp/gcash-merchants-auth/index.html?merchantId=2188",
    }
    assert checkout_app.gcash_authorization_url(payload) == payload["confirm_return_url"]


def test_gcash_authorization_url_rejects_redirect_only_payload() -> None:
    assert checkout_app.gcash_authorization_url(
        {"next_action": {"url": "https://checkoutshopper-live.adyen.com/checkoutshopper/checkoutPaymentRedirect"}}
    ) == ""


def test_gcash_payment_url_accepts_valid_adyen_handoff() -> None:
    adyen_url = (
        "https://checkoutshopper-live.adyen.com/checkoutshopper/"
        "checkoutPaymentRedirect?redirectData=payload"
    )

    assert checkout_app.gcash_payment_url({}, {"next_action": {"url": adyen_url}}) == adyen_url
    assert checkout_app.gcash_payment_url(
        {}, {"next_action": {"url": "https://evil.example/checkoutPaymentRedirect"}},
    ) == ""


def test_gcash_payment_url_prefers_final_gcash_authorization() -> None:
    gcash_url = "https://m.gcash.com/gcashapp/gcash-merchants-auth/index.html?siteId=1"
    adyen_url = "https://checkoutshopper-live.adyen.com/checkoutshopper/checkoutPaymentRedirect?redirectData=x"

    assert checkout_app.gcash_payment_url(
        {"confirm_return_url": gcash_url}, {"next_action": {"url": adyen_url}},
    ) == gcash_url


def test_gcash_payment_method_selection_prefers_provider_specific_cpmt() -> None:
    payload = {
        "custom_payment_methods": [
            {"id": "cpmt_paypal", "name": "PayPal"},
            {"id": "cpmt_gcash", "name": "GCash wallet"},
        ],
    }

    assert checkout_app.custom_payment_method_id_for(payload, "gcash") == "cpmt_gcash"


def test_gcash_payment_method_selection_allows_only_unlabelled_cpmt() -> None:
    assert checkout_app.custom_payment_method_id_for(
        {"custom_payment_methods": [{"id": "cpmt_only"}]}, "gcash",
    ) == "cpmt_only"
    assert checkout_app.custom_payment_method_id_for(
        {"custom_payment_methods": [{"id": "cpmt_one"}, {"id": "cpmt_two"}]}, "gcash",
    ) == ""
    assert checkout_app.custom_payment_method_id_for(
        {"custom_payment_methods": [{"id": "cpmt_paypal", "name": "PayPal"}]}, "gcash",
    ) == ""


def test_gcash_session_poll_waits_for_gcash_method() -> None:
    states = iter([
        {"custom_payment_methods": [{"id": "cpmt_paypal", "name": "PayPal"}]},
        {"custom_payment_methods": [{"id": "cpmt_gcash", "name": "GCash"}]},
    ])
    with (
        patch.object(checkout_app, "fetch_custom_checkout_session", side_effect=lambda *_args: next(states)) as fetch,
        patch.object(checkout_app.time, "sleep"),
    ):
        result = checkout_app.fetch_custom_checkout_session_with_retry(
            object(), "token", "oaics_1", "openai_ie", "device",
            required_provider="gcash", attempts=3,
        )

    assert checkout_app.custom_payment_method_id_for(result, "gcash") == "cpmt_gcash"
    assert fetch.call_count == 2


def test_gcash_callback_parser_accepts_query_fragment_and_validates_session() -> None:
    callback = (
        "https://chatgpt.com/checkout/verify?checkout_session_id=oaics_12345678&"
        "redirectResult=abc%2F123#redirectData=ignored"
    )
    parsed = checkout_app.parse_gcash_callback(callback, "oaics_12345678")
    assert parsed["checkout_session_id"] == "oaics_12345678"
    assert parsed["redirectResult"] == "abc/123"


def test_gcash_callback_parser_rejects_wrong_checkout_session() -> None:
    import pytest

    with pytest.raises(ValueError, match="SESSION_MISMATCH"):
        checkout_app.parse_gcash_callback(
            {"checkout_session_id": "oaics_other", "redirectResult": "result"},
            "oaics_expected",
        )


class _FakeResponse:
    status_code = 200
    text = '{"status":"success"}'

    def json(self):
        return {"status": "success"}


class _FakeHTTP:
    def __init__(self):
        self.calls = []
        self.cookies = type("Cookies", (), {"set": lambda *_args, **_kwargs: None})()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse()


def test_gcash_continuation_uses_same_checkout_session_and_redirect_result() -> None:
    http = _FakeHTTP()
    result = checkout_app.continue_custom_checkout_method(
        http, "at", "oaics_12345678", "openai_ie", "abc/123", "device", "did",
    )
    assert result["status"] == "success"
    url, request = http.calls[0]
    assert url.endswith("custom_payment_method/continue")
    assert request["json"] == {
        "checkout_session_id": "oaics_12345678",
        "action_result": {"redirectResult": "abc/123"},
    }
