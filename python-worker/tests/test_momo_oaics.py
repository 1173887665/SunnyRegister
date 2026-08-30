from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402
import provider_checkout  # noqa: E402


def test_momo_custom_payment_method_matching_strips_cpmt_prefix() -> None:
    payload = {
        "custom_payment_methods": [
            {"id": "cpmt_card", "name": "Card"},
            {"id": "cpmt_momo", "name": "MoMo"},
        ],
    }

    assert checkout_app.custom_payment_methods_for(payload, "momo") == [payload["custom_payment_methods"][1]]
    assert provider_checkout._has_provider_method(["cpmt_momo"], "momo")


def test_momo_cs_live_rechecks_after_vn_tax_refresh() -> None:
    initial_ctx = {
        "checkout_amount": 1,
        "currency": "vnd",
        "payment_method_types": ["card"],
    }
    refreshed_ctx = {
        "checkout_amount": 1,
        "currency": "vnd",
        "payment_method_types": ["card", "momo"],
    }
    billing = provider_checkout.default_billing("VN", "user@example.com")

    with (
        patch.object(
            provider_checkout.sc,
            "init_checkout",
            side_effect=[({}, "2026-test", initial_ctx), ({}, "2026-test", refreshed_ctx)],
        ) as init_checkout,
        patch.object(provider_checkout.sc, "fetch_elements_session"),
        patch.object(provider_checkout.sc, "update_tax_region"),
        patch.object(provider_checkout.sc, "snapshot_billing"),
        patch("provider_checkout.create_provider_payment_method", return_value="pm_momo") as create_method,
        patch("provider_checkout.confirm_provider_payment", return_value={}) as confirm,
        patch("provider_checkout.extract_provider_result", return_value={"provider_redirect_url": "https://momo.example/pay"}),
    ):
        result = provider_checkout.stripe_to_provider(
            object(),
            "cs_live_test",
            "momo",
            billing=billing,
            country="VN",
            stage1={"publishable_key": "pk_live_test", "processor_entity": "openai_llc"},
            log=lambda _message: None,
        )

    assert init_checkout.call_count == 2
    create_method.assert_called_once()
    assert confirm.call_args.args[6] is refreshed_ctx
    assert result["provider_redirect_url"] == "https://momo.example/pay"


def test_momo_stage1_requests_redirect_capability_and_defers_trial_campaign() -> None:
    payload = checkout_app.checkout_payload(
        {
            "plan": "plus",
            "link_type": "momo",
            "country": "VN",
            "currency": "VND",
            "checkout_country": "VN",
            "checkout_currency": "VND",
            "use_promo": True,
            "promo_campaign": "plus-1-month-free",
            "promo_on_create": False,
            "checkout_ui_mode": "redirect",
        },
        {},
    )

    assert payload["checkout_ui_mode"] == "redirect"
    assert payload["billing_details"] == {"country": "VN", "currency": "VND"}
    assert "promo_campaign" not in payload


def test_momo_authorization_url_accepts_expected_stripe_handoff() -> None:
    url = (
        "https://pm-redirects.stripe.com/authorize/"
        "acct_1HOrSwC6h1nxGoI3/sa_nonce_V9sheJQ8YBaT9uRLR4gw2ilYZiC8pWl"
    )

    assert checkout_app.is_valid_momo_authorization_url(url)
    assert checkout_app.momo_authorization_url({"next_action": {"url": url}}) == url


def test_momo_authorization_url_rejects_checkout_and_other_provider_urls() -> None:
    assert not checkout_app.is_valid_momo_authorization_url(
        "https://chatgpt.com/checkout/openai_ie/oaics_example"
    )
    assert not checkout_app.is_valid_momo_authorization_url(
        "https://pm-redirects.stripe.com/authorize/acct_test/not_a_nonce"
    )


def test_momo_attempts_request_redirect_and_accept_oaics_fallback() -> None:
    store = object.__new__(checkout_app.JobStore)
    state = {"status": "running", "error": "", "result": None}
    strategies: list[tuple[str, str, bool]] = []
    store.cancelled = lambda _job_id: False
    store.get = lambda _job_id: dict(state)
    store.update = lambda _job_id, **fields: state.update(fields)
    store.log = lambda _job_id, _message: None
    store._record_success = lambda _job_id, _result: None

    def run_single(_job_id: str, attempt_options: dict) -> None:
        strategies.append((
            str(attempt_options["checkout_ui_mode"]),
            str(attempt_options["local_method_strategy"]),
            bool(attempt_options["promo_on_create"]),
        ))
        if len(strategies) >= 4:
            state.update(status="done", result={})
        else:
            state.update(status="error", error="MOMO_METHOD_UNAVAILABLE: oaics_test")

    store._run_single = run_single
    store._run_locked("job-momo", {
        "retry_count": 3,
        "link_type": "momo",
        "use_promo": True,
        "country": "VN",
        "checkout_country": "VN",
        "entry_proxies": ["http://promotion:8001"],
        "exit_proxies": ["http://checkout:9001"],
        "paired_proxy_rotation": True,
    })

    assert strategies == [
        ("redirect", "late_promo", False),
        ("redirect", "late_promo", False),
        ("redirect", "late_promo", False),
        ("redirect", "late_promo", False),
    ]
