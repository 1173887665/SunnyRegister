from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402
from provider_checkout import (  # noqa: E402
    PROVIDER_DEFAULTS,
    default_billing,
    extract_provider_result,
)


MIDTRANS_V3 = "https://app.midtrans.com/snap/v3/redirection/123e4567-e89b-12d3-a456-426614174000"
MIDTRANS_V4 = "https://app.midtrans.com/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174001"
MIDTRANS_V4_LINKING = MIDTRANS_V4 + "#/gopay-tokenization/linking"


def test_gopay_midtrans_url_accepts_reference_snap_versions() -> None:
    assert checkout_app.is_valid_gopay_midtrans_url(MIDTRANS_V3)
    assert checkout_app.is_valid_gopay_midtrans_url(MIDTRANS_V4 + "?source=chatgpt")
    assert checkout_app.is_valid_gopay_midtrans_url(MIDTRANS_V4_LINKING)


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


def test_generic_gopay_result_preserves_midtrans_linking_fragment() -> None:
    result = checkout_app.require_gopay_midtrans_result({
        "provider_redirect_url": MIDTRANS_V4_LINKING,
        "next_action_type": "redirect_to_url",
    })
    assert result["provider_redirect_url"] == MIDTRANS_V4_LINKING
    assert result["gopay_midtrans_url"] == MIDTRANS_V4_LINKING
    assert result["checkout_url"] == MIDTRANS_V4_LINKING


def test_generic_gopay_result_rejects_non_midtrans_redirect() -> None:
    try:
        checkout_app.require_gopay_midtrans_result({
            "provider_redirect_url": "https://chatgpt.com/checkout/verify",
        })
    except RuntimeError as exc:
        assert str(exc).startswith("GOPAY_MIDTRANS_LINK_MISSING")
    else:
        raise AssertionError("普通 Checkout 链接不应被判定为 GoPay 成功结果")


def test_generic_provider_result_reads_redirect_to_url() -> None:
    result = extract_provider_result({
        "next_action": {
            "type": "redirect_to_url",
            "redirect_to_url": {"url": MIDTRANS_V4_LINKING},
        },
    }, "gopay")
    assert result["provider_redirect_url"] == MIDTRANS_V4_LINKING


def test_gopay_method_selection_prefers_gopay_cpmt() -> None:
    payload = {
        "custom_payment_methods": [
            {"id": "cpmt_paypal", "name": "PayPal"},
            {"id": "cpmt_gopay", "name": "GoPay wallet"},
        ],
    }
    assert checkout_app.custom_payment_method_id_for(payload, "gopay") == "cpmt_gopay"


def test_gopay_method_selection_accepts_protocol_aliases_but_not_other_cpmt() -> None:
    payload = {
        "custom_payment_methods": [
            {"id": "cpmt_unknown", "name": "wallet"},
            {"id": "cpmt_alias", "paymentMethodType": "gopay-tokenization"},
            {"id": "cpmt_p24", "name": "Przelewy24"},
        ],
    }
    assert checkout_app.custom_payment_method_id_for(payload, "gopay") == "cpmt_alias"
    assert checkout_app.custom_payment_methods_for(payload, "gopay") == [payload["custom_payment_methods"][1]]


def test_gopay_confirm_retries_only_blocked_responses() -> None:
    responses = [
        RuntimeError("CUSTOM_CONFIRM_BLOCKED: transient"),
        RuntimeError("CUSTOM_CONFIRM_BLOCKED: transient"),
        {"status": "success"},
    ]

    def fake_confirm(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    with patch.object(checkout_app, "confirm_custom_checkout_method", side_effect=fake_confirm) as confirm:
        result = checkout_app.confirm_custom_checkout_method_with_retry(
            object(), "token", "oaics_test", "openai_ie", "cpmt_gopay",
            "http://proxy:1", "device", "did", max_retries=2,
        )
    assert result == {"status": "success"}
    assert confirm.call_count == 3


def test_gopay_confirm_does_not_retry_non_blocked_errors() -> None:
    with patch.object(
        checkout_app,
        "confirm_custom_checkout_method",
        side_effect=RuntimeError("确认 GoPay 支付方式失败：HTTP 400"),
    ) as confirm:
        try:
            checkout_app.confirm_custom_checkout_method_with_retry(
                object(), "token", "oaics_test", "openai_ie", "cpmt_gopay",
                "http://proxy:1", "device", "did", max_retries=3,
            )
        except RuntimeError as exc:
            assert "HTTP 400" in str(exc)
        else:
            raise AssertionError("非 blocked 错误不应重试")
    assert confirm.call_count == 1


def test_gopay_checkout_preserves_method_from_creation_response() -> None:
    creation = {
        "custom_payment_methods": [
            {"id": "cpmt_card", "name": "Card"},
            {"id": "cpmt_gopay", "name": "GoPay"},
        ],
    }
    refreshed = {
        "amount_total": 0,
        "currency": "IDR",
        "custom_payment_methods": [{"id": "cpmt_card", "name": "Card"}],
    }
    with patch.object(
        checkout_app,
        "fetch_custom_checkout_session",
        return_value=refreshed,
    ) as fetch:
        result = checkout_app.fetch_custom_checkout_session_with_retry(
            object(), "token", "oaics_test", "openai_ie", "device",
            attempts=3,
            required_provider="gopay",
            preserve_payment_methods_from=creation,
        )

    fetch.assert_called_once()
    assert checkout_app.custom_payment_method_id_for(result, "gopay") == "cpmt_gopay"
    assert result["amount_total"] == 0


def test_gopay_checkout_payload_delays_promo_until_method_is_published() -> None:
    options = {
        "plan": "plus",
        "link_type": "gopay",
        "country": "ID",
        "currency": "IDR",
        "checkout_country": "ID",
        "checkout_currency": "IDR",
        "use_promo": True,
        "promo_campaign": "plus-1-month-free",
        "promo_on_create": False,
        "checkout_ui_mode": "redirect",
    }
    payload = checkout_app.checkout_payload(options, {})
    assert "promo_campaign" not in payload
    assert payload["checkout_ui_mode"] == "redirect"


def test_gopay_attempt_alternates_checkout_modes_before_applying_promo() -> None:
    store = object.__new__(checkout_app.JobStore)
    state = {"status": "running", "error": "", "result": None}
    calls = 0
    strategies: list[tuple[bool, str]] = []
    store.cancelled = lambda _job_id: False
    store.get = lambda _job_id: dict(state)
    store.update = lambda _job_id, **fields: state.update(fields)
    store.log = lambda _job_id, _message: None
    store._record_success = lambda _job_id, _result: None

    def run_single(_job_id: str, attempt_options: dict) -> None:
        nonlocal calls
        calls += 1
        strategies.append((
            bool(attempt_options["promo_on_create"]),
            str(attempt_options["checkout_ui_mode"]),
        ))
        if calls >= 2:
            state.update(status="done", result={})
        else:
            state.update(status="error", error="GOPAY_METHOD_UNAVAILABLE")

    store._run_single = run_single
    store._run_locked("job-gopay", {
        "retry_count": 1,
        "link_type": "gopay",
        "use_promo": True,
        "country": "ID",
        "checkout_country": "ID",
        "entry_proxies": ["http://promotion:8001"],
        "exit_proxies": ["http://checkout:9001"],
        "paired_proxy_rotation": True,
    })

    assert strategies == [(False, "redirect"), (False, "custom")]


def test_gopay_defaults_use_indonesia_billing() -> None:
    assert PROVIDER_DEFAULTS["gopay"] == {"country": "ID", "currency": "IDR"}
    billing = default_billing("ID", "user@example.com")
    assert billing["email"] == "user@example.com"
    assert billing["address"]["country"] == "ID"
    assert billing["address"]["city"] == "Jakarta"
    assert billing["address"]["postal_code"] == "10310"
