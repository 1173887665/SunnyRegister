from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402
import stripe_checkout  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.text = ""
        self.headers = {}

    def json(self) -> dict:
        return self.payload


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
