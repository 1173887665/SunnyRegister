"""Protocol card-payment flow for the account-opening pipeline.

The implementation mirrors the documented sequence while keeping card data,
tokens and customer identifiers in process memory only.  Callers receive a
small result summary; logs contain stage/status information and masked values.
"""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

try:
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - optional dependency fallback
    curl_requests = None

try:
    import requests as plain_requests
except Exception:  # pragma: no cover - optional dependency fallback
    plain_requests = None


APP_BASE = "https://chatgpt.com"
STRIPE_BASE = "https://api.stripe.com"
CHECKOUT_URL = f"{APP_BASE}/backend-api/payments/checkout"
STRIPE_VERSION = "2025-03-31.basil"
STRIPE_BETAS = (
    "2025-03-31.basil; checkout_server_update_beta=v1; "
    "checkout_manual_approval_preview=v1"
)
DEFAULT_TIMEOUT = 60
STRIPE_HCAPTCHA_SITE_KEY = "463b917e-e264-403f-ad34-34af0ee10294"
STRIPE_HCAPTCHA_URL = (
    "https://b.stripecdn.com/stripethirdparty-srv/assets/"
    "v33.5/HCaptchaInvisible.html"
)
KNOWN_SETUP_PUBLISHABLE_KEYS = {
    "KslHRdbaPg": "pk_live_51Pj377KslHRdbaPgTJYjThzH3f5dt1N1vK7LUp0qh0yNSarhfZ6nfbG7FFlh8KLxVkvdMWN5o6Mc4Vda6NHaSnaV00C2Sbl8Zs",
    "C6h1nxGoI3": "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRacViovU3kLKvpkjh7IqkW00iXQsjo3n",
}


class CardPaymentError(RuntimeError):
    """A stage-labelled payment error with safe diagnostic text."""


@dataclass(slots=True)
class CardPaymentConfig:
    token: str
    account_id: str = ""
    country: str = "US"
    currency: str = "USD"
    promo_campaign: str = "plus-1-month-free"
    billing: dict[str, str] = field(default_factory=dict)
    card: dict[str, str] = field(default_factory=dict)
    session_token: str = ""
    cookies: list[dict[str, Any]] = field(default_factory=list)
    device_id: str = ""
    fingerprint_profile: dict[str, str] = field(default_factory=dict)
    timeout: int = DEFAULT_TIMEOUT
    max_setup_confirm_attempts: int = 1
    hcaptcha_token: str = ""
    checkout_id: str = ""
    payment_page_id: str = ""
    processor_entity: str = ""
    publishable_key: str = ""
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    captcha_provider: str = ""
    captcha_key: str = ""
    captcha_api_url: str = ""
    hcaptcha_site_key: str = STRIPE_HCAPTCHA_SITE_KEY
    hcaptcha_website_url: str = STRIPE_HCAPTCHA_URL
    bind_country: str = "US"
    bind_currency: str = "USD"
    strong_bind_direct: bool = True
    stop_after_bind: bool = False
    flow_mode: str = "full"
    payment_method_id: str = ""
    card_last4: str = ""
    fast_verify: bool = False


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mask(value: Any, head: int = 8, tail: int = 4) -> str:
    raw = _text(value)
    if not raw:
        return ""
    if len(raw) <= head + tail:
        return "***"
    return f"{raw[:head]}…{raw[-tail:]}"


def _safe_error(value: Any) -> str:
    raw = re.sub(r"\s+", " ", _text(value))
    raw = re.sub(
        r"(?i)(client_secret|confirmation_token|access_token|authorization|"
        r"card\]?\[?(?:number|cvc|cvv)|password)([=:])[^&\s,}]+",
        r"\1\2<REDACTED>",
        raw,
    )
    return raw[:500]


def _log(logger: Callable[[str], None], message: str) -> None:
    try:
        logger(_safe_error(message))
    except Exception:
        pass


def _check_cancel(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled and is_cancelled():
        raise CardPaymentError("payment cancelled")


def _response_json(response: Any) -> dict[str, Any]:
    try:
        value = response.json() or {}
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _response_error(response: Any) -> str:
    status = int(getattr(response, "status_code", 0) or 0)
    payload = _response_json(response)
    if payload:
        error = payload.get("error")
        if isinstance(error, dict):
            code = _text(error.get("code"))
            message = _text(error.get("message"))
            return f"HTTP {status} {code} {message}".strip()
        return f"HTTP {status} {_text(payload.get('message') or payload.get('detail'))}".strip()
    return f"HTTP {status}".strip()


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _find_key(payload: Any, names: tuple[str, ...]) -> str:
    wanted = {item.lower() for item in names}
    for item in _walk(payload):
        for key, value in item.items():
            if key.lower() in wanted and isinstance(value, (str, int, float)):
                text = _text(value)
                if text:
                    return text
    return ""


def _find_identifier(payload: Any, prefixes: tuple[str, ...]) -> str:
    for item in _walk(payload):
        for value in item.values():
            if not isinstance(value, (str, int, float)):
                continue
            text = _text(value)
            if any(text.startswith(prefix) for prefix in prefixes):
                return text
    return ""


def _find_client_secret(payload: Any) -> str:
    value = _find_key(payload, ("client_secret", "clientSecret"))
    if value:
        return value
    for item in _walk(payload):
        for value in item.values():
            text = _text(value)
            if "_secret_" in text and text.startswith(("seti_", "pi_", "cs_")):
                return text
    return ""


def _setup_intent_id(payload: Any, client_secret: str = "") -> str:
    """Prefer the SetupIntent id over the similarly-prefixed client secret."""
    direct = _find_key(payload, ("setup_intent_id", "setupIntentId", "id"))
    if direct.startswith("seti_") and "_secret_" not in direct:
        return direct
    secret_base = _text(client_secret).split("_secret_", 1)[0]
    if secret_base.startswith("seti_"):
        return secret_base
    candidate = _find_identifier(payload, ("seti_",))
    if "_secret_" in candidate:
        candidate = candidate.split("_secret_", 1)[0]
    return candidate if candidate.startswith("seti_") else ""


def _require_setup_succeeded(payload: Any, stage: str) -> str:
    """Reject HTTP-200 Stripe responses that still need action or failed."""
    status = _text(_find_key(payload, ("status",))).lower()
    if status == "succeeded":
        return status
    if status == "requires_action":
        raise CardPaymentError(f"{stage}: requires_action (3DS verification required)")
    if status:
        raise CardPaymentError(f"{stage}: unexpected SetupIntent status {status}")
    raise CardPaymentError(f"{stage}: missing SetupIntent status")


def _publishable_key_for_setup(client_secret: str, fallback: str = "") -> str:
    secret = _text(client_secret)
    for fragment, key in KNOWN_SETUP_PUBLISHABLE_KEYS.items():
        if fragment in secret:
            return key
    return _text(fallback)


def _checkout_id(payload: Any) -> str:
    return _find_key(
        payload,
        ("checkout_session_id", "checkoutSessionId", "session_id", "sessionId"),
    ) or _find_identifier(payload, ("oaics_", "cs_live_", "cs_test_", "cs_"))


def _processor_entity(payload: Any, country: str) -> str:
    return _find_key(payload, ("processor_entity", "processorEntity")) or (
        "openai_llc" if country.upper() in {"US", "AU"} else "openai_ie"
    )


def _card_fields(card: dict[str, Any]) -> dict[str, str]:
    number = re.sub(r"\D", "", _text(card.get("card_number") or card.get("number")))
    cvc = re.sub(r"\D", "", _text(card.get("cvv") or card.get("cvc")))
    month = re.sub(r"\D", "", _text(card.get("exp_month") or card.get("month"))).zfill(2)
    year = re.sub(r"\D", "", _text(card.get("exp_year") or card.get("year")))
    if len(year) == 2:
        year = f"20{year}"
    if not number or not cvc or len(month) != 2 or len(year) != 4:
        raise CardPaymentError("invalid card fields")
    if len(number) < 12 or len(number) > 19:
        raise CardPaymentError("invalid card number length")
    if len(cvc) not in {3, 4}:
        raise CardPaymentError("invalid card security code length")
    return {"number": number, "cvc": cvc, "exp_month": month, "exp_year": year}


def _billing_fields(config: CardPaymentConfig) -> dict[str, str]:
    source = {str(k): _text(v) for k, v in (config.billing or {}).items()}
    return {
        "name": source.get("name") or "",
        "email": source.get("email"),
        "line1": source.get("line1") or source.get("address") or "",
        "line2": source.get("line2"),
        "city": source.get("city"),
        "state": source.get("state"),
        "postal_code": source.get("postal_code") or source.get("zip") or "",
        "country": source.get("country") or "",
        "phone": source.get("phone"),
    }


def _payment_card_last4(config: CardPaymentConfig) -> str:
    external_last4 = re.sub(r"\D", "", _text(config.card_last4))[-4:]
    if len(external_last4) == 4:
        return external_last4
    if config.card:
        return _card_fields(config.card)["number"][-4:]
    return ""


def _new_session(config: CardPaymentConfig, proxy: str = "") -> Any:
    fingerprint = dict(config.fingerprint_profile or {})
    impersonate = _text(fingerprint.get("tls_impersonate")) or "chrome146"
    if curl_requests is not None:
        session = curl_requests.Session(impersonate=impersonate)
    elif plain_requests is not None:
        session = plain_requests.Session()
    else:
        raise CardPaymentError("missing HTTP dependency: install curl_cffi")
    if hasattr(session, "trust_env"):
        session.trust_env = False
    device_id = _text(config.device_id) or str(uuid.uuid4())
    user_agent = _text(fingerprint.get("ua")) or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    )
    major = _text(fingerprint.get("major")) or "146"
    platform = _text(fingerprint.get("platform")) or "Windows"
    sec_ch_ua = _text(fingerprint.get("sec_ch_ua")) or (
        f'"Not;A=Brand";v="8", "Chromium";v="{major}", '
        f'"Google Chrome";v="{major}"'
    )
    accept_language = _text(fingerprint.get("accept_language")) or (
        _text(config.locale) or "en-US"
    )
    oai_language = _text(fingerprint.get("oai_language")) or (
        _text(config.locale) or "en-US"
    )
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": accept_language,
        "Authorization": f"Bearer {_text(config.token)}",
        "Origin": APP_BASE,
        "Referer": f"{APP_BASE}/",
        "Content-Type": "application/json",
        "oai-device-id": device_id,
        "oai-language": oai_language,
        "sec-ch-ua": sec_ch_ua,
        "sec-ch-ua-mobile": _text(fingerprint.get("mobile")) or "?0",
        "sec-ch-ua-platform": f'"{platform.strip(chr(34))}"',
        "priority": "u=1, i",
    })
    if fingerprint:
        session.headers.update({
            "sec-ch-ua-arch": f'"{_text(fingerprint.get("arch")) or "x86"}"',
            "sec-ch-ua-bitness": f'"{_text(fingerprint.get("bitness")) or "64"}"',
            "sec-ch-ua-model": '""',
            "sec-ch-ua-full-version": f'"{_text(fingerprint.get("full")) or major}"',
            "sec-ch-ua-full-version-list": _text(
                fingerprint.get("sec_ch_ua_full_version_list")
            ) or sec_ch_ua,
            "sec-ch-ua-platform-version": (
                f'"{_text(fingerprint.get("platform_version")) or "15.0.0"}"'
            ),
        })
    if _text(proxy):
        session.proxies = {"http": proxy, "https": proxy}
    if hasattr(session, "cookies"):
        try:
            session.cookies.set(
                "oai-did",
                device_id,
                domain=".chatgpt.com",
                path="/",
            )
        except Exception:
            pass
    if _text(config.session_token) and hasattr(session, "cookies"):
        try:
            session.cookies.set(
                "__Secure-next-auth.session-token",
                _text(config.session_token),
                domain=".chatgpt.com",
                path="/",
            )
        except Exception:
            pass
    for cookie in config.cookies or []:
        if not isinstance(cookie, dict) or not _text(cookie.get("name")):
            continue
        try:
            session.cookies.set(
                _text(cookie.get("name")),
                _text(cookie.get("value")),
                domain=_text(cookie.get("domain")) or ".chatgpt.com",
                path=_text(cookie.get("path")) or "/",
            )
        except Exception:
            pass
    return session


def _app_headers(referer: str = "", route: str = "") -> dict[str, str]:
    headers = {
        "Origin": APP_BASE,
        "Referer": referer or f"{APP_BASE}/",
        "Content-Type": "application/json",
    }
    if route:
        headers["x-openai-target-path"] = route
        headers["x-openai-target-route"] = route
    return headers


def _stripe_headers(
    publishable_key: str,
    referer: str,
    *,
    stripe_version: str = "",
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {publishable_key}",
        "Origin": "https://js.stripe.com",
        "Referer": "https://js.stripe.com/",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if _text(stripe_version):
        headers["Stripe-Version"] = _text(stripe_version)
    return headers


def _post_json(
    session: Any,
    url: str,
    body: dict[str, Any],
    *,
    timeout: int,
    stage: str,
    referer: str = "",
    route: str = "",
) -> dict[str, Any]:
    response = session.post(
        url,
        json=body,
        headers=_app_headers(referer, route),
        timeout=timeout,
    )
    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise CardPaymentError(f"{stage}: {_response_error(response)}")
    return _response_json(response)


def _post_form(
    session: Any,
    url: str,
    body: dict[str, Any],
    *,
    key: str,
    referer: str,
    timeout: int,
    stage: str,
    stripe_version: str = "",
) -> tuple[Any, dict[str, Any]]:
    response = session.post(
        url,
        data=urlencode(body, doseq=True),
        headers=_stripe_headers(key, referer, stripe_version=stripe_version),
        timeout=timeout,
    )
    return response, _response_json(response)


def _checkout_create(
    session: Any,
    config: CardPaymentConfig,
    *,
    country: str = "",
    currency: str = "",
    promo_campaign: str | None = None,
) -> dict[str, Any]:
    checkout_country = (_text(country) or config.country).upper()
    checkout_currency = (_text(currency) or config.currency).upper()
    body: dict[str, Any] = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {
            "country": checkout_country,
            "currency": checkout_currency,
        },
        "checkout_ui_mode": "custom",
    }
    campaign = config.promo_campaign if promo_campaign is None else promo_campaign
    if _text(campaign):
        body["promo_campaign"] = {
            "promo_campaign_id": _text(campaign),
            "is_coupon_from_query_param": False,
        }
    return _post_json(
        session,
        CHECKOUT_URL,
        body,
        timeout=config.timeout,
        stage="checkout create",
        route="/backend-api/payments/checkout",
    )


def _init_payment_page(session: Any, config: CardPaymentConfig, payment_page_id: str, publishable_key: str, processor_entity: str) -> tuple[dict[str, Any], str]:
    stripe_js_id = str(uuid.uuid4())
    page = f"{APP_BASE}/checkout/{processor_entity}/{config.checkout_id or payment_page_id}"
    locale = _text(config.locale) or "zh-CN"
    body = {
        "browser_locale": locale,
        "browser_timezone": _text(config.timezone) or "Asia/Shanghai",
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": locale,
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "auto",
        "elements_options_client[saved_payment_method][enable_redisplay]": "auto",
        "key": publishable_key,
        "_stripe_version": STRIPE_BETAS,
    }
    response, payload = _post_form(
        session,
        f"{STRIPE_BASE}/v1/payment_pages/{payment_page_id}/init",
        body,
        key=publishable_key,
        referer=page,
        timeout=config.timeout,
        stage="payment page init",
    )
    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise CardPaymentError(f"payment page init: {_response_error(response)}")
    return payload, stripe_js_id


def _create_elements_session(
    session: Any,
    config: CardPaymentConfig,
    init_payload: dict[str, Any],
    payment_page_id: str,
    publishable_key: str,
    stripe_js_id: str,
) -> dict[str, Any]:
    options = (
        init_payload.get("elements_options")
        if isinstance(init_payload.get("elements_options"), dict)
        else {}
    )
    amount = options.get("amount")
    currency = _text(options.get("currency") or init_payload.get("currency"))
    payment_config = _text(options.get("payment_method_configuration"))
    method_types = options.get("payment_method_types")
    if not isinstance(method_types, list) or not method_types:
        method_types = ["card", "paypal"]
    params: dict[str, Any] = {
        "client_betas[0]": "custom_checkout_server_updates_1",
        "client_betas[1]": "custom_checkout_manual_approval_1",
        "deferred_intent[mode]": "subscription",
        "deferred_intent[amount]": str(amount if amount is not None else "2000"),
        "deferred_intent[currency]": currency or _text(config.bind_currency).lower() or "usd",
        "deferred_intent[setup_future_usage]": "off_session",
        "currency": currency or _text(config.bind_currency).lower() or "usd",
        "key": publishable_key,
        "_stripe_version": STRIPE_BETAS,
        "elements_init_source": "custom_checkout",
        "referrer_host": "chatgpt.com",
        "stripe_js_id": stripe_js_id,
        "locale": (_text(config.locale).split("-", 1)[0] or "zh"),
        "type": "deferred_intent",
        "checkout_session_id": payment_page_id,
    }
    for index, method_type in enumerate(method_types[:4]):
        params[f"deferred_intent[payment_method_types][{index}]"] = _text(
            method_type
        )
    if payment_config:
        params[
            "deferred_intent[payment_method_configuration][id]"
        ] = payment_config
    response = session.get(
        f"{STRIPE_BASE}/v1/elements/sessions",
        params=params,
        headers=_stripe_headers(publishable_key, "https://js.stripe.com/"),
        timeout=config.timeout,
    )
    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise CardPaymentError(f"elements session: {_response_error(response)}")
    payload = _response_json(response)
    if not payload:
        raise CardPaymentError("elements session: empty response")
    return payload


def _setup_confirm(
    session: Any,
    config: CardPaymentConfig,
    init_payload: dict[str, Any],
    checkout_id: str,
    processor: str,
    publishable_key: str,
    setup_id: str,
    client_secret: str,
    stripe_js_id: str,
    *,
    attempt: int,
) -> tuple[Any, dict[str, Any]]:
    external_payment_method = _text(config.payment_method_id)
    billing = _billing_fields(config)
    elements_session_id = _find_identifier(init_payload, ("elements_session_",))
    wallet_config_id = _find_key(init_payload, ("wallet_config_id",))
    guid = f"{uuid.uuid4()}{uuid.uuid4().hex[:6]}"
    muid = f"{uuid.uuid4()}{uuid.uuid4().hex[:6]}"
    sid = f"{uuid.uuid4()}{uuid.uuid4().hex[:6]}"
    body: dict[str, Any] = {
        "set_as_default_payment_method": "true",
        "expected_payment_method_type": "card",
        "use_stripe_sdk": "true",
        "key": publishable_key,
        "_stripe_version": STRIPE_VERSION,
        "client_attribution_metadata[client_session_id]": stripe_js_id,
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "card-element",
        "client_attribution_metadata[merchant_integration_version]": "2017",
        "client_secret": client_secret,
    }
    if external_payment_method:
        body["payment_method"] = external_payment_method
    else:
        card = _card_fields(config.card)
        body.update({
            "payment_method_data[type]": "card",
            "payment_method_data[billing_details][name]": billing["name"],
            "payment_method_data[allow_redisplay]": "always",
            "payment_method_data[card][number]": card["number"],
            "payment_method_data[card][cvc]": card["cvc"],
            "payment_method_data[card][exp_month]": card["exp_month"],
            "payment_method_data[card][exp_year]": card["exp_year"],
            "payment_method_data[guid]": guid,
            "payment_method_data[muid]": muid,
            "payment_method_data[sid]": sid,
            "payment_method_data[pasted_fields]": "number,exp,cvc",
            "payment_method_data[payment_user_agent]": "stripe.js/3704557c13; stripe-js-v3/3704557c13; card-element",
            "payment_method_data[referrer]": APP_BASE,
            "payment_method_data[time_on_page]": str(random.randint(300_000, 750_000)),
            "payment_method_data[client_attribution_metadata][client_session_id]": stripe_js_id,
            "payment_method_data[client_attribution_metadata][merchant_integration_source]": "elements",
            "payment_method_data[client_attribution_metadata][merchant_integration_subtype]": "card-element",
            "payment_method_data[client_attribution_metadata][merchant_integration_version]": "2017",
        })
        if billing["email"]:
            body["payment_method_data[billing_details][email]"] = billing["email"]
        for field in ("line1", "line2", "city", "state", "postal_code", "country"):
            value = billing.get(field, "")
            if value:
                body[f"payment_method_data[billing_details][address][{field}]"] = (
                    value.upper() if field == "country" else value
                )
        if billing["phone"]:
            body["payment_method_data[billing_details][phone]"] = billing["phone"]
        if billing["postal_code"]:
            body["payment_method_data[pasted_fields]"] = "number,exp,cvc,zip"
    if _text(config.hcaptcha_token):
        body["radar_options[hcaptcha_token]"] = _text(config.hcaptcha_token)
    if elements_session_id and not external_payment_method:
        body["payment_method_data[client_attribution_metadata][elements_session_id]"] = elements_session_id
    if wallet_config_id and not external_payment_method:
        body["payment_method_data[client_attribution_metadata][wallet_config_id]"] = wallet_config_id
        body["client_attribution_metadata[wallet_config_id]"] = wallet_config_id
    response, payload = _post_form(
        session,
        f"{STRIPE_BASE}/v1/setup_intents/{setup_id}/confirm",
        body,
        key=publishable_key,
        referer=f"{APP_BASE}/checkout/{processor}/{checkout_id}",
        timeout=config.timeout,
        stage=f"setup confirm attempt {attempt}",
    )
    return response, payload


def _prepare_hcaptcha_token(
    config: CardPaymentConfig,
    *,
    proxy: str,
    logger: Callable[[str], None],
) -> str:
    supplied = _text(config.hcaptcha_token)
    if supplied:
        return supplied
    provider = _text(config.captcha_provider).lower()
    api_key = _text(config.captcha_key)
    api_url = _text(config.captcha_api_url)
    if not provider or not api_key:
        return ""
    if provider != "aixiangshu" and "aixiangshu.com" not in api_url.lower():
        _log(logger, f"Stripe hCaptcha provider not wired for card flow: {provider}")
        return ""
    try:
        from .paypal_plus.signup import _solve_aixiangshu_gateway

        token, _solution = _solve_aixiangshu_gateway(
            api_url=api_url or "https://sub.aixiangshu.com/captcha",
            api_key=api_key,
            task={
                "type": "HCaptchaTask",
                "websiteURL": _text(config.hcaptcha_website_url)
                or STRIPE_HCAPTCHA_URL,
                "websiteKey": _text(config.hcaptcha_site_key)
                or STRIPE_HCAPTCHA_SITE_KEY,
                "isInvisible": True,
                "proxy": _text(proxy),
            },
            timeout=max(60, min(300, int(config.timeout or DEFAULT_TIMEOUT))),
            label="stripe-hcaptcha",
            token_fields=("gRecaptchaResponse", "hcaptchaToken", "token"),
        )
    except Exception as exc:
        raise CardPaymentError(
            f"Stripe hCaptcha solve failed: {type(exc).__name__}"
        ) from exc
    if not token:
        raise CardPaymentError("Stripe hCaptcha solve returned no token")
    _log(logger, "Stripe hCaptcha token ready")
    return token


def run_card_payment(
    config: CardPaymentConfig,
    *,
    proxy: str = "",
    logger: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    session_factory: Callable[[CardPaymentConfig, str], Any] | None = None,
    refresh_checkout: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run bind-card -> checkout-confirm -> subscription verification."""
    log = logger or (lambda _message: None)
    config.token = _text(config.token)
    if not config.token:
        raise CardPaymentError("missing access token")
    external_payment_method = _text(config.payment_method_id)
    if external_payment_method:
        if not re.fullmatch(r"pm_[A-Za-z0-9_-]+", external_payment_method):
            raise CardPaymentError("invalid browser PaymentMethod id")
    else:
        _card_fields(config.card)
    session = session_factory(config, proxy) if session_factory else _new_session(config, proxy)
    checkout: dict[str, Any] = {}
    init_payload: dict[str, Any] = {}
    try:
        _check_cancel(is_cancelled)
        bind_checkout: dict[str, Any] = {}
        if _text(config.checkout_id):
            checkout = {
                "checkout_session_id": _text(config.checkout_id),
                "processor_entity": _text(config.processor_entity),
                "publishable_key": _text(config.publishable_key),
            }
            log(f"using canonical zero-amount checkout: {_mask(config.checkout_id)}")
        else:
            checkout = _checkout_create(session, config)
        checkout_id = _checkout_id(checkout)
        if not checkout_id:
            raise CardPaymentError("checkout create: missing checkout id")
        processor = _processor_entity(checkout, config.country)
        publishable_key = _text(config.publishable_key) or _find_key(
            checkout, ("publishable_key", "publishableKey")
        )
        config.checkout_id = checkout_id
        account_id = _text(config.account_id) or _find_key(
            checkout, ("account_id", "accountId")
        )
        if not account_id:
            raise CardPaymentError("payment method prepare: missing account id")

        stripe_js_id = str(uuid.uuid4())
        bind_step_key = (
            "payment_prepare" if _text(config.flow_mode) == "link_pay" else "bind"
        )
        bind_step_label = (
            "正在验证账号支付方式"
            if bind_step_key == "payment_prepare"
            else "正在执行绑卡"
        )
        log(f"FLOW_STEP:{bind_step_key}:start:{bind_step_label}")
        if config.strong_bind_direct:
            if not checkout_id.startswith("oaics_"):
                raise CardPaymentError(
                    "strong bind: expected PH oaics checkout context"
                )
            checkout_context = session.get(
                f"{APP_BASE}/backend-api/payments/checkout/{processor}/{checkout_id}",
                headers=_app_headers(
                    f"{APP_BASE}/checkout/{processor}/{checkout_id}",
                    "/backend-api/payments/checkout/{processor_entity}/{checkout_session_id}",
                ),
                timeout=config.timeout,
            )
            if int(getattr(checkout_context, "status_code", 0) or 0) >= 400:
                raise CardPaymentError(
                    f"strong bind checkout context: {_response_error(checkout_context)}"
                )
            payment_method_prepare_payload = _post_json(
                session,
                f"{APP_BASE}/backend-api/payments/payment_method",
                {"account_id": account_id},
                timeout=config.timeout,
                stage="payment method prepare",
                route="/backend-api/payments/payment_method",
            )
            stripe_context = {
                "payment_method_prepare": payment_method_prepare_payload
            }
            setup_secret = _find_client_secret(payment_method_prepare_payload)
            setup_id = _setup_intent_id(
                payment_method_prepare_payload, setup_secret
            )
            publishable_key = _publishable_key_for_setup(
                setup_secret, publishable_key
            )
            log(f"PH oaics strong-bind SetupIntent ready: {_mask(setup_id)}")
        else:
            payment_page_id = _text(config.payment_page_id)
            if not payment_page_id:
                bind_checkout = _checkout_create(
                    session,
                    config,
                    country=_text(config.bind_country) or "US",
                    currency=_text(config.bind_currency) or "USD",
                    promo_campaign="",
                )
                payment_page_id = _find_identifier(
                    bind_checkout, ("cs_live_", "cs_test_", "cs_")
                )
            if not payment_page_id.startswith(("cs_live_", "cs_test_", "cs_")):
                raise CardPaymentError("bind checkout: invalid Stripe payment page id")
            publishable_key = _find_key(
                bind_checkout, ("publishable_key", "publishableKey")
            ) or publishable_key or _find_identifier(
                bind_checkout, ("pk_live_", "pk_test_")
            )
            init_payload, stripe_js_id = _init_payment_page(
                session, config, payment_page_id, publishable_key, processor
            )
            elements_payload = _create_elements_session(
                session,
                config,
                init_payload,
                payment_page_id,
                publishable_key,
                stripe_js_id,
            )
            payment_method_prepare_payload = _post_json(
                session,
                f"{APP_BASE}/backend-api/payments/payment_method",
                {"account_id": account_id},
                timeout=config.timeout,
                stage="payment method prepare",
                route="/backend-api/payments/payment_method",
            )
            stripe_context = {
                "init": init_payload,
                "elements": elements_payload,
                "payment_method_prepare": payment_method_prepare_payload,
            }
            setup_secret = (
                _find_client_secret(payment_method_prepare_payload)
                or _find_client_secret(elements_payload)
                or _find_client_secret(init_payload)
            )
            setup_id = (
                _setup_intent_id(payment_method_prepare_payload, setup_secret)
                or _setup_intent_id(elements_payload, setup_secret)
                or _setup_intent_id(init_payload, setup_secret)
            )
            log(f"Stripe payment-page SetupIntent ready: {_mask(setup_id)}")
        if not setup_id or not setup_secret:
            raise CardPaymentError("strong bind: missing SetupIntent credentials")
        if not publishable_key.startswith(("pk_live_", "pk_test_")):
            raise CardPaymentError("strong bind: missing matching publishable key")

        config.hcaptcha_token = _prepare_hcaptcha_token(
            config,
            proxy=proxy,
            logger=log,
        )

        confirm_response = None
        confirm_payload: dict[str, Any] = {}
        max_attempts = max(1, min(3, int(config.max_setup_confirm_attempts or 1)))
        for attempt in range(1, max_attempts + 1):
            _check_cancel(is_cancelled)
            confirm_response, confirm_payload = _setup_confirm(
                session,
                config,
                stripe_context,
                checkout_id,
                processor,
                publishable_key,
                setup_id,
                setup_secret,
                stripe_js_id,
                attempt=attempt,
            )
            status = int(getattr(confirm_response, "status_code", 0) or 0)
            if status == 200:
                break
            if status == 402 and attempt < max_attempts:
                log(f"setup confirm returned 402; retry {attempt + 1}/{max_attempts}")
                continue
            raise CardPaymentError(f"setup confirm: {_response_error(confirm_response)}")
        _require_setup_succeeded(confirm_payload, "setup confirm")
        payment_method_id = _find_key(confirm_payload, ("payment_method", "payment_method_id")) or _find_identifier(confirm_payload, ("pm_",))
        if not payment_method_id:
            raise CardPaymentError("setup confirm: missing PaymentMethod")
        log(f"payment method created: {_mask(payment_method_id)}")

        customer = _find_identifier(
            {"stripe_context": stripe_context, "setup_confirm": confirm_payload},
            ("cus_",),
        ) or _find_identifier(bind_checkout or checkout, ("cus_",))
        if not customer:
            for payload in (
                confirm_payload,
                stripe_context,
                bind_checkout,
                checkout,
            ):
                candidate = _find_key(payload, ("customer", "customer_id", "customerId"))
                if isinstance(candidate, str) and candidate:
                    customer = candidate
                    break

        app_payment_methods_status = 0
        if account_id:
            payment_methods = session.get(
                f"{APP_BASE}/backend-api/payments/payment_methods",
                params={"account_id": account_id},
                headers=_app_headers(
                    f"{APP_BASE}/",
                    "/backend-api/payments/payment_methods",
                ),
                timeout=config.timeout,
            )
            app_payment_methods_status = int(getattr(payment_methods, "status_code", 0) or 0)
            if app_payment_methods_status >= 400:
                raise CardPaymentError(f"payment methods sync: {_response_error(payment_methods)}")

        stripe_payment_methods_status = 0
        if customer:
            stripe_payment_methods = session.get(
                f"{STRIPE_BASE}/v1/payment_methods",
                params={"customer": customer, "type": "card", "limit": 30},
                headers=_stripe_headers(
                    publishable_key,
                    f"{APP_BASE}/checkout/{processor}/{checkout_id}",
                ),
                timeout=config.timeout,
            )
            stripe_payment_methods_status = int(
                getattr(stripe_payment_methods, "status_code", 0) or 0
            )
            if stripe_payment_methods_status >= 400:
                raise CardPaymentError(
                    f"stripe payment methods: {_response_error(stripe_payment_methods)}"
                )

        log(
            f"FLOW_STEP:{bind_step_key}:done:支付方式 {_mask(payment_method_id)} 已确认并同步"
        )

        if config.stop_after_bind:
            log("card bind completed; stopped before checkout refresh and payment")
            return {
                "ok": True,
                "checkout_id": checkout_id,
                "processor_entity": processor,
                "payment_method": _mask(payment_method_id),
                "card_last4": _payment_card_last4(config),
                "setup_status": _text(confirm_payload.get("status")) or "succeeded",
                "app_payment_methods_status": app_payment_methods_status,
                "stripe_payment_methods_status": stripe_payment_methods_status,
                "bind_only": True,
            }

        if refresh_checkout is not None:
            _check_cancel(is_cancelled)
            refreshed_checkout = refresh_checkout() or {}
            refreshed_id = _checkout_id(refreshed_checkout)
            refreshed_amount = _text(refreshed_checkout.get("amount"))
            if not refreshed_id.startswith("oaics_"):
                raise CardPaymentError(
                    "checkout refresh: missing canonical oaics checkout id"
                )
            if refreshed_amount != "0":
                raise CardPaymentError(
                    f"checkout refresh: expected zero amount, got {refreshed_amount or 'unknown'}"
                )
            checkout_id = refreshed_id
            processor = _processor_entity(refreshed_checkout, config.country)
            refreshed_key = _text(
                refreshed_checkout.get("_publishable_key")
            )
            if refreshed_key.startswith(("pk_live_", "pk_test_")):
                publishable_key = refreshed_key
            refreshed_currency = _text(refreshed_checkout.get("currency"))
            if refreshed_currency:
                config.currency = refreshed_currency.upper()
            config.checkout_id = checkout_id
            log(f"checkout refreshed after card bind: {_mask(checkout_id)}")

        log("FLOW_STEP:payment:start:正在提交支付确认")

        checkout_context = session.get(
            f"{APP_BASE}/backend-api/payments/checkout/{processor}/{checkout_id}",
            headers=_app_headers(
                f"{APP_BASE}/checkout/{processor}/{checkout_id}",
                "/backend-api/payments/checkout/{processor_entity}/{checkout_session_id}",
            ),
            timeout=config.timeout,
        )
        if int(getattr(checkout_context, "status_code", 0) or 0) >= 400:
            raise CardPaymentError(
                f"canonical checkout restore: {_response_error(checkout_context)}"
            )

        billing_country = _billing_fields(config).get("country") or config.country.upper()
        _post_json(
            session,
            f"{APP_BASE}/backend-api/payments/checkout/taxes",
            {
                "checkout_session_id": checkout_id,
                "processor_entity": processor,
                "billing_country": billing_country.upper(),
                "billing_currency": config.currency.upper(),
                "currency": config.currency.upper(),
            },
            timeout=config.timeout,
            stage="checkout taxes",
            referer=f"{APP_BASE}/checkout/{processor}/{checkout_id}",
            route="/backend-api/payments/checkout/taxes",
        )

        # Billing details are attached while the PaymentMethod is created by
        # the SetupIntent confirmation above. Updating /v1/payment_methods/{id}
        # afterwards is a secret-key-only operation and is redundant here.
        log("billing details supplied during SetupIntent confirmation")

        token_body: dict[str, Any] = {
            "payment_method": payment_method_id,
            "setup_future_usage": "off_session",
            "set_as_default_payment_method": "true",
            "client_context[currency]": config.currency.lower(),
            "client_context[mode]": "subscription",
            "client_context[payment_method_types][0]": "card",
            "client_context[payment_method_types][1]": "link",
            "client_attribution_metadata[client_session_id]": stripe_js_id,
            "client_attribution_metadata[merchant_integration_source]": "elements",
            "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
            "client_attribution_metadata[merchant_integration_version]": "2021",
            "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
            "client_attribution_metadata[payment_method_selection_flow]": "automatic",
            "client_attribution_metadata[merchant_integration_additional_elements][0]": "expressCheckout",
            "client_attribution_metadata[merchant_integration_additional_elements][1]": "payment",
            "client_attribution_metadata[merchant_integration_additional_elements][2]": "address",
            "key": publishable_key,
        }
        if customer:
            token_body["client_context[customer]"] = customer
        elements_session_id = _find_key(
            stripe_context, ("elements_session_id", "elementsSessionId")
        ) or _find_identifier(stripe_context, ("elements_session_",))
        elements_session_config_id = _find_key(
            stripe_context,
            ("elements_session_config_id", "elementsSessionConfigId", "config_id"),
        )
        if elements_session_id:
            token_body["client_attribution_metadata[elements_session_id]"] = (
                elements_session_id
            )
        if elements_session_config_id:
            token_body[
                "client_attribution_metadata[elements_session_config_id]"
            ] = elements_session_config_id
        response, token_payload = _post_form(
            session,
            f"{STRIPE_BASE}/v1/confirmation_tokens",
            token_body,
            key=publishable_key,
            referer=f"{APP_BASE}/checkout/{processor}/{checkout_id}",
            timeout=config.timeout,
            stage="confirmation token",
            stripe_version=STRIPE_BETAS,
        )
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise CardPaymentError(f"confirmation token: {_response_error(response)}")
        confirmation_token = _find_key(token_payload, ("confirmation_token", "confirmationToken")) or _find_identifier(token_payload, ("ctoken_", "ct_"))
        if not confirmation_token:
            raise CardPaymentError("confirmation token: missing token")

        app_confirm_payload = _post_json(
            session,
            f"{APP_BASE}/backend-api/payments/checkout/confirm",
            {},
            timeout=config.timeout,
            stage="checkout confirm",
            referer=f"{APP_BASE}/checkout/{processor}/{checkout_id}",
            route="/backend-api/payments/checkout/confirm",
        )
        final_secret = (
            _find_client_secret(app_confirm_payload)
            or _find_client_secret(token_payload)
            or _find_client_secret(confirm_payload)
            or setup_secret
        )
        final_setup_id = (
            _setup_intent_id(app_confirm_payload, final_secret)
            or _setup_intent_id(token_payload, final_secret)
            or _setup_intent_id(confirm_payload, final_secret)
            or setup_id
        )
        if not final_setup_id or not final_secret:
            raise CardPaymentError("checkout confirm: missing final SetupIntent credentials")
        return_url = (
            f"{APP_BASE}/checkout/verify?stripe_session_id={checkout_id}"
            f"&processor_entity={processor}&plan_type=plus"
        )
        final_body = {
            "client_secret": final_secret,
            "confirmation_token": confirmation_token,
            "key": publishable_key,
            "return_url": return_url,
            "use_stripe_sdk": "true",
            "_stripe_version": STRIPE_BETAS,
            "client_attribution_metadata[client_session_id]": stripe_js_id,
            "client_attribution_metadata[merchant_integration_source]": "l1",
        }
        response, final_payload = _post_form(
            session,
            f"{STRIPE_BASE}/v1/setup_intents/{final_setup_id}/confirm",
            final_body,
            key=publishable_key,
            referer=f"{APP_BASE}/checkout/{processor}/{checkout_id}",
            timeout=config.timeout,
            stage="final setup confirm",
        )
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise CardPaymentError(f"final setup confirm: {_response_error(response)}")
        final_setup_status = _require_setup_succeeded(
            final_payload, "final setup confirm"
        )
        _check_cancel(is_cancelled)

        log("FLOW_STEP:payment:done:支付确认已提交")
        log("FLOW_STEP:verify:start:正在校验 Checkout 与订阅状态")

        verify = session.get(
            f"{APP_BASE}/backend-api/payments/checkout/{processor}/{checkout_id}",
            headers=_app_headers(
                f"{APP_BASE}/checkout/verify",
                "/backend-api/payments/checkout/{processor_entity}/{checkout_session_id}",
            ),
            timeout=config.timeout,
        )
        if int(getattr(verify, "status_code", 0) or 0) >= 400:
            raise CardPaymentError(f"checkout verify: {_response_error(verify)}")
        verify_page = None
        success_data = None
        auth_session = None
        if not config.fast_verify:
            verify_page = session.get(
                f"{APP_BASE}/checkout/verify",
                params={
                    "stripe_session_id": checkout_id,
                    "processor_entity": processor,
                    "plan_type": "plus",
                },
                headers=_app_headers(f"{APP_BASE}/checkout/{processor}/{checkout_id}"),
                timeout=config.timeout,
            )
            success_data = session.get(
                f"{APP_BASE}/payments/success.data",
                params={"stripe_session_id": checkout_id},
                headers=_app_headers(f"{APP_BASE}/checkout/verify"),
                timeout=config.timeout,
            )
            auth_session = session.get(
                f"{APP_BASE}/api/auth/session",
                params={"reason": "checkout_success"},
                headers=_app_headers(f"{APP_BASE}/checkout/verify"),
                timeout=config.timeout,
            )
        subscription = None
        subscription_plan = ""
        # The subscription endpoint can lag a successful zero-amount SetupIntent
        # by a fraction of a second.  Poll briefly, but never report success from
        # HTTP 200 alone: the account must actually show the Plus plan.
        for subscription_attempt in range(1, 4):
            _check_cancel(is_cancelled)
            subscription = session.get(
                f"{APP_BASE}/backend-api/subscriptions",
                params={"account_id": account_id},
                headers=_app_headers(
                    f"{APP_BASE}/", "/backend-api/subscriptions"
                ),
                timeout=config.timeout,
            )
            subscription_http = int(
                getattr(subscription, "status_code", 0) or 0
            )
            if subscription_http >= 400:
                raise CardPaymentError(
                    f"subscription verify: {_response_error(subscription)}"
                )
            subscription_payload = _response_json(subscription)
            subscription_plan = _text(subscription_payload.get("plan_type"))
            if subscription_plan.lower() == "plus":
                break
            if subscription_attempt < 3:
                time.sleep(0.35)
        if subscription_plan.lower() != "plus":
            raise CardPaymentError(
                "subscription verify: Plus plan was not activated"
            )
        log("payment succeeded and checkout verified")
        log("FLOW_STEP:verify:done:Checkout 与订阅状态校验完成")
        return {
            "ok": True,
            "checkout_id": checkout_id,
            "processor_entity": processor,
            "payment_method": _mask(payment_method_id),
            "card_last4": _payment_card_last4(config),
            "setup_status": final_setup_status,
            "verify_status": int(getattr(verify, "status_code", 0) or 0),
            "verify_page_status": int(getattr(verify_page, "status_code", 0) or 0),
            "success_data_status": int(getattr(success_data, "status_code", 0) or 0),
            "auth_session_status": int(getattr(auth_session, "status_code", 0) or 0),
            "app_payment_methods_status": app_payment_methods_status,
            "stripe_payment_methods_status": stripe_payment_methods_status,
            "subscription_status": int(getattr(subscription, "status_code", 0) or 0) if subscription is not None else 0,
            "subscription_plan": subscription_plan,
        }
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
