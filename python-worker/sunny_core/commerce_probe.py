from __future__ import annotations

import time
from typing import Any

from curl_cffi import requests as curl_requests


TRIAL_URL = (
    "https://chatgpt.com/backend-api/promo_campaign/check_coupon"
    "?coupon=plus-1-month-free&is_coupon_from_query_param=true"
)
CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"


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
    raw = payload.get("payment_method_types") or payload.get("custom_payment_methods") or []
    if not isinstance(raw, list):
        return methods
    for item in raw:
        method = str((item.get("type") or item.get("id") or "") if isinstance(item, dict) else item).strip().lower()
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
    session = curl_requests.Session(impersonate="chrome136")
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


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
    promotion_session = _session(str(promotion_proxy_url or proxy_url).strip())
    checkout_session = _session(str(checkout_proxy_url or proxy_url).strip())
    headers = _headers(token)
    result: dict[str, Any] = {
        "trial": {"state": "", "http": 0, "error": ""},
        "checkout": {"kind": "", "payment_methods": [], "http": 0, "error": ""},
    }
    try:
        try:
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
            checkout_headers = {**headers, "content-type": "application/json", "x-openai-target-path": "/backend-api/payments/checkout", "x-openai-target-route": "/backend-api/payments/checkout"}
            checkout_body = {
                "entry_point": "all_plans_pricing_modal",
                "plan_name": "chatgptplusplan",
                "billing_details": {"country": billing_country, "currency": billing_currency},
                "checkout_ui_mode": "custom",
            }
            checkout_response = _request_with_retry(lambda: checkout_session.post(CHECKOUT_URL, json=checkout_body, headers=checkout_headers, timeout=45))
            checkout_payload, checkout_error = _safe_json(checkout_response)
            session_id = str(checkout_payload.get("checkout_session_id") or checkout_payload.get("session_id") or checkout_payload.get("id") or "")
            kind = (
                "oaics" if session_id.startswith("oaics_")
                else "cs_test" if session_id.startswith("cs_test_")
                else "cs_live" if session_id.startswith("cs_")
                else ""
            )
            result["checkout"] = {
                "kind": kind,
                "payment_methods": _payment_methods(checkout_payload),
                "http": checkout_response.status_code,
                "error": checkout_error,
            }
        except Exception as exc:
            result["checkout"]["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        return result
    finally:
        promotion_session.close()
        checkout_session.close()
