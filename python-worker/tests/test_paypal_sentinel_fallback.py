from __future__ import annotations

import pytest

from tools.pay153_checkout.sentinel_fallback import resolve_payment_sentinel_headers


async def sentinel_success(*_args, **_kwargs) -> dict[str, str]:
    return {"OpenAI-Sentinel-Token": "token"}


async def missing_turnstile_proof(*_args, **_kwargs) -> dict[str, str]:
    raise RuntimeError(
        "Sentinel token generation failed after fresh-session retry: "
        "required t proof was not generated"
    )


async def unrelated_failure(*_args, **_kwargs) -> dict[str, str]:
    raise RuntimeError("OpenAI Checkout HTTP 403")


def test_returns_generated_sentinel_headers() -> None:
    assert resolve_payment_sentinel_headers(
        sentinel_success, "proxy", "chatgpt_checkout", "device", "did",
    ) == {"OpenAI-Sentinel-Token": "token"}


def test_paypal_can_fallback_when_required_t_proof_is_missing() -> None:
    logs: list[str] = []

    headers = resolve_payment_sentinel_headers(
        missing_turnstile_proof,
        "proxy",
        "chatgpt_checkout",
        "device",
        "did",
        allow_fallback=True,
        log=logs.append,
    )

    assert headers == {}
    assert "不携带 Sentinel 头" in logs[0]
    assert "required t proof was not generated" in logs[0]


def test_strict_routes_still_fail_when_required_t_proof_is_missing() -> None:
    with pytest.raises(RuntimeError, match="required t proof"):
        resolve_payment_sentinel_headers(
            missing_turnstile_proof,
            "proxy",
            "chatgpt_checkout",
            "device",
            "did",
        )


def test_fallback_does_not_hide_checkout_api_errors() -> None:
    with pytest.raises(RuntimeError, match="Checkout HTTP 403"):
        resolve_payment_sentinel_headers(
            unrelated_failure,
            "proxy",
            "chatgpt_checkout",
            "device",
            "did",
            allow_fallback=True,
        )
