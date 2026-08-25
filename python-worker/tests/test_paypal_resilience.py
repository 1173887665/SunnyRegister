from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402
import provider_checkout  # noqa: E402
import stripe_checkout  # noqa: E402


@pytest.mark.parametrize("message", [
    "SSLError: Failed to perform, curl: (35) Recv failure: Connection reset by peer",
    "curl: (35) SSL connect error",
    "proxy TLS handshake failed",
])
def test_proxy_ssl_error_is_classified_for_route_rotation(message: str) -> None:
    assert checkout_app._is_proxy_ssl_error(message) is True


@pytest.mark.parametrize("message", [
    "Timeout: Failed to perform, curl: (28) Operation timed out after 60002 milliseconds with 0 bytes received",
    "curl: (28) Operation timed out",
])
def test_proxy_timeout_is_classified_for_route_rotation(message: str) -> None:
    assert checkout_app._is_proxy_timeout_error(message) is True


def test_non_transport_error_is_not_classified_as_proxy_ssl_error() -> None:
    assert checkout_app._is_proxy_ssl_error("OAICS_PAYPAL_METHOD_UNAVAILABLE") is False


def test_proxy_route_label_redacts_credentials() -> None:
    label = checkout_app.proxy_route_label("http://user:secret@example.test:8080")
    assert label.startswith("http://example.test:8080#route=")
    assert "secret" not in label


def test_ideal_checkout_payload_uses_custom_session_for_oaics_compatibility() -> None:
    options = {
        "plan": "plus",
        "link_type": "ideal",
        "country": "NL",
        "currency": "EUR",
        "checkout_country": "NL",
        "checkout_currency": "EUR",
        "use_promo": True,
    }

    payload = checkout_app.checkout_payload(options, {})

    assert payload["checkout_ui_mode"] == "custom"
    assert "promo_campaign" not in payload


def test_other_local_checkout_payload_keeps_custom_session_mode() -> None:
    options = {
        "plan": "plus",
        "link_type": "twint",
        "country": "CH",
        "currency": "CHF",
        "checkout_country": "CH",
        "checkout_currency": "CHF",
        "use_promo": True,
    }

    payload = checkout_app.checkout_payload(options, {})

    assert payload["checkout_ui_mode"] == "custom"


def test_momo_trial_payload_carries_verified_campaign_on_first_checkout() -> None:
    options = {
        "plan": "plus",
        "link_type": "momo",
        "country": "VN",
        "currency": "VND",
        "checkout_country": "VN",
        "checkout_currency": "VND",
        "use_promo": True,
        "promo_campaign": "plus-1-month-free",
        "promo_on_create": True,
    }

    payload = checkout_app.checkout_payload(options, {})

    assert payload["checkout_ui_mode"] == "custom"
    assert payload["promo_campaign"] == {
        "promo_campaign_id": "plus-1-month-free",
        "is_coupon_from_query_param": False,
    }


def test_gcash_trial_payload_carries_verified_campaign_on_first_checkout() -> None:
    options = {
        "plan": "plus",
        "link_type": "gcash",
        "country": "PH",
        "currency": "PHP",
        "checkout_country": "PH",
        "checkout_currency": "PHP",
        "use_promo": True,
        "promo_campaign": "plus-1-month-free",
        "promo_on_create": True,
    }

    payload = checkout_app.checkout_payload(options, {})

    assert payload["checkout_ui_mode"] == "custom"
    assert payload["billing_details"] == {"country": "PH", "currency": "PHP"}
    assert payload["promo_campaign"] == {
        "promo_campaign_id": "plus-1-month-free",
        "is_coupon_from_query_param": False,
    }


def test_ideal_success_requires_signed_pay_ideal_transaction_url() -> None:
    valid = (
        "https://pay.ideal.nl/transactions/https%3A%2F%2Ftx.ideal.nl%2F2%2FTEST"
        "?sig=SIGNED"
    )
    assert provider_checkout.is_valid_ideal_payment_url(valid) is True
    assert provider_checkout.is_valid_ideal_payment_url(
        "https://chatgpt.com/checkout/openai_ie/oaics_test"
    ) is False
    assert provider_checkout.is_valid_ideal_payment_url(
        "https://pay.ideal.nl/transactions/https%3A%2F%2Ftx.ideal.nl%2F2%2FTEST"
    ) is False
    assert provider_checkout.is_valid_ideal_payment_url(
        "https://pay.ideal.nl/transactions/https%3A%2F%2Fexample.com%2F2%2FTEST?sig=SIGNED"
    ) is False


@pytest.mark.parametrize("transport_error", [
    "SSLError: curl: (35) Recv failure: Connection reset by peer",
    "Timeout: Failed to perform, curl: (28) Operation timed out after 60002 milliseconds with 0 bytes received",
])
def test_transport_retry_respects_configured_attempt_budget_and_rotates_routes(transport_error: str) -> None:
    store = object.__new__(checkout_app.JobStore)
    state = {"status": "running", "error": "", "result": None}
    logs: list[str] = []
    routes: list[tuple[str, str]] = []

    store.cancelled = lambda _job_id: False
    store.get = lambda _job_id: dict(state)

    def update(_job_id: str, **fields):
        state.update(fields)

    store.update = update
    store.log = lambda _job_id, message: logs.append(message)
    store._record_success = lambda _job_id, _result: None

    def run_single(_job_id: str, attempt_options: dict):
        routes.append((attempt_options["fixed_entry_proxy"], attempt_options["fixed_exit_proxy"]))
        state.update(status="running", error=transport_error)

    store._run_single = run_single
    options = {
        "retry_count": 1,
        "link_type": "hosted",
        "entry_proxies": ["http://entry-1:8001", "http://entry-2:8002", "http://entry-3:8003", "http://entry-4:8004"],
        "exit_proxies": ["http://exit-1:9001", "http://exit-2:9002", "http://exit-3:9003", "http://exit-4:9004"],
        "paired_proxy_rotation": True,
    }

    with patch.object(checkout_app.time, "sleep"):
        store._run_locked("job-1", options)

    assert len(routes) == 2
    assert len(set(routes)) == 2
    assert state["status"] == "error"
    assert not any("达到 3 次代理切换上限" in message for message in logs)


def test_paypal_transport_retry_preserves_strategy_and_business_attempts() -> None:
    store = object.__new__(checkout_app.JobStore)
    state = {"status": "running", "error": "", "result": None}
    logs: list[str] = []
    strategies: list[bool] = []

    store.cancelled = lambda _job_id: False
    store.get = lambda _job_id: dict(state)

    def update(_job_id: str, **fields):
        state.update(fields)

    store.update = update
    store.log = lambda _job_id, message: logs.append(message)
    store._record_success = lambda _job_id, _result: None

    def run_single(_job_id: str, attempt_options: dict):
        strategies.append(bool(attempt_options["promo_on_create"]))
        errors = [
            "Timeout: Failed to perform, curl: (28) Operation timed out after 60002 milliseconds",
            "RuntimeError: PayPal business path did not return an approval URL",
            'RuntimeError: 应用 Plus 优惠失败：HTTP 403 {"detail":"This promotion is not available."}',
        ]
        error = errors[len(strategies) - 1]
        state.update(status="running", error=error)

    store._run_single = run_single
    options = {
        "retry_count": 2,
        "link_type": "paypal",
        "use_promo": True,
        "country": "GB",
        "entry_proxies": [
            "http://promo-1:8001", "http://promo-2:8002", "http://promo-3:8003",
        ],
        "exit_proxies": [
            "http://checkout-1:9001", "http://checkout-2:9002", "http://checkout-3:9003",
        ],
        "paired_proxy_rotation": True,
    }

    with patch.object(checkout_app.time, "sleep"):
        store._run_locked("job-paypal", options)

    assert strategies == [True, True, False]
    assert any("沿用相同 PayPal 优惠策略" in message for message in logs)


def test_retry_count_is_failures_after_initial_attempt() -> None:
    store = object.__new__(checkout_app.JobStore)
    state = {"status": "running", "error": "", "result": None}
    attempts: list[int] = []

    store.cancelled = lambda _job_id: False
    store.get = lambda _job_id: dict(state)
    store.update = lambda _job_id, **fields: state.update(fields)
    store.log = lambda _job_id, _message: None
    store._record_success = lambda _job_id, _result: None

    def run_single(_job_id: str, _options: dict):
        attempts.append(1)
        state.update(status="running", error="temporary checkout failure")

    store._run_single = run_single
    options = {
        "retry_count": 3,
        "link_type": "hosted",
        "entry_proxies": ["http://entry:8001"],
        "exit_proxies": ["http://exit:9001"],
    }

    with patch.object(checkout_app.time, "sleep"):
        store._run_locked("job-retry-budget", options)

    assert len(attempts) == 4


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.text = ""
        self.headers = {}

    def json(self) -> dict:
        return self.payload


def test_manual_approval_reuses_checkout_device_id() -> None:
    captured: dict = {}

    class FakeHttp:
        def post(self, _url: str, **kwargs):
            captured.update(kwargs)
            return FakeResponse({"result": "approved"})

    result = stripe_checkout.approve_submission(
        FakeHttp(),
        "token",
        "cs_live_test",
        "openai_ie",
        lambda _message: None,
        device_id="device-test-123",
    )

    assert result == {"result": "approved"}
    assert captured["headers"]["OAI-Device-Id"] == "device-test-123"


def test_paypal_payment_and_approval_billing_follow_checkout_country() -> None:
    checkout_billing = {
        "name": "Test User",
        "email": "test@example.com",
        "address": {
            "country": "DE",
            "line1": "Potsdamer Platz 1",
            "city": "Berlin",
            "postal_code": "10785",
        },
    }
    proxy_billing = {
        "name": "Test User",
        "email": "test@example.com",
        "address": {
            "country": "BR",
            "line1": "Avenida Paulista 1000",
            "city": "Sao Paulo",
            "postal_code": "01310100",
        },
    }
    logs: list[str] = []
    init_ctx = {
        "checkout_amount": 0,
        "currency": "eur",
        "payment_method_types": ["card", "paypal"],
    }
    redirect = "https://www.paypal.com/agreements/approve?ba_token=BA-TEST"

    with (
        patch.object(
            stripe_checkout,
            "init_checkout",
            return_value=({"total_summary": {"due": 0}}, stripe_checkout.STRIPE_VERSION_BASE, init_ctx),
        ),
        patch.object(stripe_checkout, "fetch_elements_session"),
        patch.object(stripe_checkout, "update_tax_region"),
        patch.object(stripe_checkout, "snapshot_billing") as snapshot,
        patch.object(stripe_checkout, "create_paypal_payment_method", return_value="pm_test") as create_pm,
        patch.object(
            stripe_checkout,
            "confirm_payment",
            return_value={"next_action": {"redirect_to_url": {"url": redirect}}},
        ) as confirm,
        patch.object(stripe_checkout, "resolve_paypal_approval_url", return_value=redirect),
    ):
        result, _pk, ctx = stripe_checkout.stripe_to_paypal_redirect(
            object(),
            "cs_live_test",
            billing=checkout_billing,
            payment_billing=proxy_billing,
            country="DE",
            chatgpt_http=object(),
            access_token="token",
            publishable_key="pk_test",
            processor_entity="openai_ie",
            require_zero_due=True,
            log=logs.append,
        )

    assert result == redirect
    assert snapshot.call_args.args[4] == checkout_billing
    assert create_pm.call_args.args[2] == checkout_billing
    assert confirm.call_args.args[6]["billing"] == checkout_billing
    assert ctx["paypal_billing_country"] == "DE"
    assert any("PaymentMethod/merchant=DE" in message for message in logs)


def test_oaics_session_retry_waits_for_delayed_payment_methods() -> None:
    responses = [
        {},
        {"custom_payment_methods": [{"id": "cpmt_card", "type": "card"}]},
        {"custom_payment_methods": [{"id": "cpmt_paypal", "type": "paypal"}]},
    ]
    logs: list[str] = []

    with patch.object(
        checkout_app,
        "fetch_custom_checkout_session",
        side_effect=responses,
    ) as fetch, patch.object(checkout_app.time, "sleep") as sleep:
        result = checkout_app.fetch_custom_checkout_session_with_retry(
            object(), "token", "cs_live_123", "openai_ie", "device",
            logs.append,
            require_paypal=True,
        )

    assert result["custom_payment_methods"][0]["id"] == "cpmt_paypal"
    assert fetch.call_count == 3
    assert sleep.call_count == 2
    assert any("尚未就绪" in message for message in logs)
    assert any("延迟就绪" in message for message in logs)


def test_oaics_custom_payment_methods_selects_ideal_only() -> None:
    payload = {
        "custom_payment_methods": [
            {"id": "cpmt_card", "type": "card"},
            {"id": "cpmt_ideal", "type": "ideal", "label": "iDEAL"},
            {"id": "legacy_ideal", "type": "ideal"},
        ]
    }
    methods = checkout_app.custom_payment_methods_for(payload, "ideal")
    assert [item["id"] for item in methods] == ["cpmt_ideal"]


def test_elements_session_records_paypal_types_for_init_probe() -> None:
    class FakeHttp:
        def get(self, *_args, **_kwargs):
            return FakeResponse({
                "session_id": "elements_session_123",
                "payment_method_specs": [
                    {"type": "card"},
                    {"type": "paypal"},
                ],
            })

    ctx = {
        "currency": "eur",
        "checkout_amount": 0,
        "payment_method_types": ["card", "paypal"],
    }
    stripe_checkout.fetch_elements_session(
        FakeHttp(), "pk_test", "cs_live_123", ctx,
        stripe_checkout.STRIPE_VERSION_BASE,
        stripe_checkout._profile("DE"), lambda _message: None,
    )

    assert ctx["elements_payment_method_types"] == ["card", "paypal"]
    assert ctx["payment_method_types"] == ["card", "paypal"]


def test_paypal_redirect_poll_retries_transient_empty_state() -> None:
    class FakeHttp:
        def __init__(self):
            self.responses = [
                FakeResponse({"state": "active"}),
                FakeResponse({
                    "next_action": {
                        "redirect_to_url": {
                            "url": "https://pm-redirects.stripe.com/authorize/test",
                        },
                    },
                }),
            ]

        def get(self, *_args, **_kwargs):
            return self.responses.pop(0)

    with patch.object(stripe_checkout.time, "sleep") as sleep:
        result = stripe_checkout.poll_paypal_redirect_light(
            FakeHttp(), "pk_test", "cs_live_123", lambda _message: None,
            max_attempts=2,
        )

    assert result == "https://pm-redirects.stripe.com/authorize/test"
    sleep.assert_called_once_with(0.8)
