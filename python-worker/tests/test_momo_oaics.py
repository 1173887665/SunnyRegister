from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402


def test_momo_stage1_defers_trial_campaign_on_redirect_checkout() -> None:
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


def test_momo_cs_live_creation_rebuilds_oaics_with_sentinel_fallback() -> None:
    closed: list[str] = []

    class FakeHttp:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    responses = [
        {"data": {"checkout_session_id": "oaics_first"}, "http": FakeHttp("first")},
        {"data": {"checkout_session_id": "cs_live_success"}, "http": FakeHttp("success")},
    ]
    calls: list[tuple[str, str, bool]] = []

    def fake_create(_token, _payload, _proxy, device_id, did, _log, **kwargs):
        calls.append((device_id, did, bool(kwargs.get("allow_sentinel_fallback"))))
        return responses.pop(0)

    generated_ids = iter(["device-2", "did-2"])
    with (
        patch.object(checkout_app, "create_checkout", side_effect=fake_create),
        patch.object(checkout_app.uuid, "uuid4", side_effect=lambda: next(generated_ids)),
    ):
        created, device_id, did = checkout_app.create_momo_cs_live_checkout(
            "token", {"checkout_ui_mode": "redirect"}, "http://proxy",
            "device-1", "did-1", lambda _message: None, attempts=2,
        )

    assert created["data"]["checkout_session_id"] == "cs_live_success"
    assert (device_id, did) == ("device-2", "did-2")
    assert calls == [
        ("device-1", "did-1", True),
        ("device-2", "did-2", True),
    ]
    assert closed == ["first"]


def test_momo_cs_live_creation_keeps_budget_after_temporary_http_500() -> None:
    class FakeHttp:
        def close(self) -> None:
            pass

    calls = 0

    def fake_create(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('OpenAI Checkout HTTP 500: {"detail":"Internal Server Error"}')
        return {"data": {"checkout_session_id": "cs_live_success"}, "http": FakeHttp()}

    generated_ids = iter(["device-2", "did-2"])
    with (
        patch.object(checkout_app, "create_checkout", side_effect=fake_create),
        patch.object(checkout_app.uuid, "uuid4", side_effect=lambda: next(generated_ids)),
    ):
        created, device_id, did = checkout_app.create_momo_cs_live_checkout(
            "token", {"checkout_ui_mode": "redirect"}, "http://proxy",
            "device-1", "did-1", lambda _message: None, attempts=2,
        )

    assert created["data"]["checkout_session_id"] == "cs_live_success"
    assert (device_id, did) == ("device-2", "did-2")
    assert calls == 2


def test_momo_cs_live_creation_does_not_retry_business_http_400() -> None:
    calls = 0

    def fake_create(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError(
            'OpenAI Checkout HTTP 400: {"detail":"Billing country must match request country."}'
        )

    with patch.object(checkout_app, "create_checkout", side_effect=fake_create):
        try:
            checkout_app.create_momo_cs_live_checkout(
                "token", {"checkout_ui_mode": "redirect"}, "http://proxy",
                "device-1", "did-1", lambda _message: None, attempts=10,
            )
        except RuntimeError as exc:
            assert "Billing country must match request country" in str(exc)
        else:
            raise AssertionError("expected the non-retryable HTTP 400 error")

    assert calls == 1


def test_momo_attempts_force_redirect_and_late_promo() -> None:
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
        if len(strategies) >= 2:
            state.update(status="done", result={})
        else:
            state.update(status="error", error="MOMO_CS_LIVE_REBUILD_EXHAUSTED")

    store._run_single = run_single
    store._run_locked("job-momo", {
        "retry_count": 1,
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
    ]
