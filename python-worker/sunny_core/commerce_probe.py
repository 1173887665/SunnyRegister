from __future__ import annotations

import time
import uuid
import sys
from pathlib import Path
from typing import Any

from curl_cffi import requests as curl_requests

from .browser_traffic import ProxyTrafficMeter, use_traffic_meter

try:
    from tools.pay153_checkout.paypal_routing import session_checkout_kind
except ImportError:  # pragma: no cover - direct module execution compatibility
    from paypal_routing import session_checkout_kind


TRIAL_URL = (
    "https://chatgpt.com/backend-api/promo_campaign/check_coupon"
    "?coupon=plus-1-month-free&is_coupon_from_query_param=true"
)
CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"


def _headers(token: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "referer": "https://chatgpt.com/",
        "user-agent": USER_AGENT,
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "oai-language": "en-US",
    }


def _safe_json(response: Any) -> tuple[dict[str, Any], str]:
    try:
        payload = response.json() or {}
        if isinstance(payload, dict):
            return payload, ""
    except Exception:
        pass
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0]
    return {}, f"HTTP {response.status_code} returned {content_type or 'non-JSON'} content"


def _payment_methods(payload: dict[str, Any]) -> list[str]:
    methods: list[str] = []
    # Checkout revisions may expose standard methods and country-specific
    # custom methods in separate fields. Keep both, including fields added by
    # future API revisions, so the backend can persist and filter unknown ones.
    for key in ("payment_method_types", "custom_payment_methods", "payment_methods", "available_payment_methods", "payment_method_specs"):
        raw = payload.get(key) or []
        if not isinstance(raw, list):
            continue
        for item in raw:
            method = str(
                (item.get("type") or item.get("id") or item.get("name") or "")
                if isinstance(item, dict) else item
            ).strip().lower()
            if method and method not in methods:
                methods.append(method)
    return methods


def _request_with_retry(request: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return request()
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
    assert last_error is not None
    raise last_error


def _session(proxy_url: str) -> Any:
    session = curl_requests.Session(impersonate="firefox144")
    try:
        session.trust_env = False
    except Exception:
        pass
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


def _task_style_checkout_probe(
    access_token: str,
    country: str,
    currency: str,
    checkout_proxy_url: str,
) -> dict[str, Any]:
    """Run the same Checkout creation path used by PayPal extraction tasks."""
    engine_dir = Path(__file__).resolve().parents[1] / "tools" / "pay153_checkout"
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    from app import checkout_payload, create_checkout

    options: dict[str, Any] = {
        "plan": "plus",
        "country": country,
        "currency": currency,
        "checkout_country": country,
        "checkout_currency": currency,
        "link_type": "paypal",
        "use_promo": False,
        "promo_campaign": "",
    }
    payload = checkout_payload(options, {})
    created = create_checkout(
        access_token,
        payload,
        checkout_proxy_url,
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        lambda _message: None,
        use_sen=True,
        use_so=True,
        allow_sentinel_fallback=True,
    )
    data = created.get("data") or {}
    session_id = str(data.get("checkout_session_id") or "")
    try:
        return {
            "kind": session_checkout_kind(session_id),
            "payment_methods": _payment_methods(data),
            "http": 200,
            "error": "",
        }
    finally:
        http = created.get("http")
        close = getattr(http, "close", None)
        if callable(close):
            close()


def probe_trial(access_token: str, proxy_url: str = "") -> dict[str, Any]:
    token = str(access_token or "").strip()
    if not token:
        return {
            "trial": {"state": "", "http": 0, "error": "missing access token"},
            "traffic": {"requests": 0, "total_bytes": 0},
        }
    selected_proxy = str(proxy_url or "").strip()
    session = _session(selected_proxy)
    meter = ProxyTrafficMeter(
        proxy_url=selected_proxy,
        tracked_proxy=bool(selected_proxy),
        operation="commerce_trial",
    )
    result: dict[str, Any] = {
        "trial": {"state": "", "http": 0, "error": ""},
        "traffic": {"requests": 0, "total_bytes": 0},
    }
    try:
        try:
            with use_traffic_meter(meter):
                response = _request_with_retry(lambda: session.get(TRIAL_URL, headers=_headers(token), timeout=30))
            payload, error = _safe_json(response)
            result["trial"] = {
                "state": str(payload.get("state") or "").strip().lower(),
                "http": response.status_code,
                "error": error,
            }
        except Exception as exc:
            result["trial"]["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        result["traffic"] = meter.snapshot()
        return result
    finally:
        session.close()


def probe_payment_methods(
    access_token: str,
    proxy_url: str = "",
    country: str = "US",
    currency: str = "USD",
) -> dict[str, Any]:
    token = str(access_token or "").strip()
    if not token:
        return {
            "checkout": {"kind": "", "payment_methods": [], "http": 0, "error": "missing access token"},
            "traffic": {"requests": 0, "total_bytes": 0},
        }
    billing_country = str(country or "US").strip().upper() or "US"
    billing_currency = str(currency or "USD").strip().upper() or "USD"
    selected_proxy = str(proxy_url or "").strip()
    meter = ProxyTrafficMeter(
        proxy_url=selected_proxy,
        tracked_proxy=bool(selected_proxy),
        operation="payment_method_probe",
    )
    result: dict[str, Any] = {
        "checkout": {"kind": "", "payment_methods": [], "http": 0, "error": ""},
        "traffic": {"requests": 0, "total_bytes": 0},
    }
    try:
        with use_traffic_meter(meter):
            result["checkout"] = _task_style_checkout_probe(
                token,
                billing_country,
                billing_currency,
                selected_proxy,
            )
    except Exception as exc:
        message = f"{type(exc).__name__}: {str(exc)[:240]}"
        status = 401 if "HTTP 401" in message else 403 if "HTTP 403" in message else 0
        result["checkout"]["http"] = status
        result["checkout"]["error"] = message
    result["traffic"] = meter.snapshot()
    return result


def probe_commerce(
    access_token: str,
    proxy_url: str = "",
    country: str = "DE",
    currency: str = "",
    *,
    promotion_proxy_url: str = "",
    checkout_proxy_url: str = "",
) -> dict[str, Any]:
    token = str(access_token or "").strip()
    if not token:
        return {"trial": {"state": "", "http": 0, "error": "missing access token"}, "checkout": {"kind": "", "payment_methods": [], "http": 0, "error": "missing access token"}}
    billing_country = str(country or "DE").strip().upper() or "DE"
    billing_currency = str(currency or ("EUR" if billing_country == "DE" else "USD")).strip().upper()
    selected_promotion_proxy = str(promotion_proxy_url or proxy_url).strip()
    selected_checkout_proxy = str(checkout_proxy_url or proxy_url).strip()
    promotion_session = _session(selected_promotion_proxy)
    promotion_meter = ProxyTrafficMeter(
        proxy_url=selected_promotion_proxy,
        tracked_proxy=bool(selected_promotion_proxy),
        operation="commerce_trial",
    )
    checkout_meter = ProxyTrafficMeter(
        proxy_url=selected_checkout_proxy,
        tracked_proxy=bool(selected_checkout_proxy),
        operation="commerce_checkout",
    )
    headers = _headers(token)
    result: dict[str, Any] = {
        "trial": {"state": "", "http": 0, "error": ""},
        "checkout": {"kind": "", "payment_methods": [], "http": 0, "error": ""},
        "traffic": {"requests": 0, "total_bytes": 0},
    }
    try:
        try:
            with use_traffic_meter(promotion_meter):
                trial_response = _request_with_retry(lambda: promotion_session.get(TRIAL_URL, headers=headers, timeout=30))
            trial_payload, trial_error = _safe_json(trial_response)
            result["trial"] = {
                "state": str(trial_payload.get("state") or "").strip().lower(),
                "http": trial_response.status_code,
                "error": trial_error,
            }
        except Exception as exc:
            result["trial"]["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"

        try:
            with use_traffic_meter(checkout_meter):
                result["checkout"] = _task_style_checkout_probe(
                    token,
                    billing_country,
                    billing_currency,
                    selected_checkout_proxy,
                )
        except Exception as exc:
            result["checkout"]["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        promotion_traffic = promotion_meter.snapshot()
        checkout_traffic = checkout_meter.snapshot()
        result["traffic"] = {
            "requests": int(promotion_traffic.get("requests") or 0) + int(checkout_traffic.get("requests") or 0),
            "total_bytes": int(promotion_traffic.get("total_bytes") or 0) + int(checkout_traffic.get("total_bytes") or 0),
        }
        return result
    finally:
        promotion_session.close()
