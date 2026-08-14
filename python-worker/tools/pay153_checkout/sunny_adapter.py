from __future__ import annotations

import re
import sys
import os
from pathlib import Path
from typing import Any

_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
os.environ.setdefault("PAY153_UPI_GO_BINARY", str(_ENGINE_DIR / "tools" / "upi_go" / "pix_extract_slot"))

from app import STORE


_TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9_.-]{40,}")
_PROXY_AUTH_RE = re.compile(r"((?:https?|socks5?)://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)


def _safe_error(value: Any) -> str:
    text = _TOKEN_RE.sub("[TOKEN]", str(value or ""))
    return _PROXY_AUTH_RE.sub(r"\1[PROXY]@", text)[:1200]


def start_checkout(payload: dict[str, Any]) -> str:
    options = {
        "token_raw": str(payload.get("token") or ""),
        "plan": str(payload.get("plan") or "plus"),
        "link_type": str(payload.get("link_type") or "hosted"),
        "country": str(payload.get("country") or "US").upper(),
        "currency": str(payload.get("currency") or "USD").upper(),
        "checkout_country": str(payload.get("country") or "US").upper(),
        "checkout_currency": str(payload.get("currency") or "USD").upper(),
        # pay153's entry pool is the Promotion route and its exit pool is the
        # billing/Checkout route. SunnyRegister exposes those pools in the
        # opposite order, so keep the translation at this adapter boundary.
        "entry_proxies": list(payload.get("promotion_proxies") or []),
        "exit_proxies": list(payload.get("checkout_proxies") or []),
        "use_promo": bool(payload.get("use_promo")),
        "promo_campaign": str(payload.get("promo_campaign") or ""),
        "promo_code": str(payload.get("promo_code") or ""),
        "workspace_name": str(payload.get("workspace_name") or "")[:80],
        "workspace_id": str(payload.get("workspace_id") or "")[:120],
        "seat_quantity": int(payload.get("seat_quantity") or 5),
        "price_interval": "year" if payload.get("price_interval") == "year" else "month",
        "credit_quantity": int(payload.get("credit_quantity") or 13),
        "ideal_bank": str(payload.get("ideal_bank") or "")[:40],
        "pix_tax_id": str(payload.get("pix_tax_id") or "")[:14],
        "pix_tax_id_auto": not bool(payload.get("pix_tax_id")),
        "pix_auto_kind": str(payload.get("pix_auto_kind") or "cpf"),
        "retry_count": min(50, max(1, int(payload.get("retry_count") or 3))),
        "paired_proxy_rotation": True,
        "use_sen": True,
        "use_so": True,
        "entry_proxy_country": str(payload.get("promo_country") or payload.get("country") or "US").upper(),
        "exit_proxy_country": str(payload.get("country") or "US").upper(),
    }
    if options["link_type"] in {"pix", "momo"}:
        options["exit_proxies"] = options["entry_proxies"]
    if options["link_type"] in {"gcash", "ph_short"} and options["country"] == "PH":
        options["entry_proxy_country"] = "US"
    if options["link_type"] == "gcash":
        options["exit_proxy_country"] = str(payload.get("promo_country") or "VN").upper()
    if options["link_type"] == "ph_short" and options["use_promo"]:
        options["exit_proxy_country"] = str(payload.get("promo_country") or "TR").upper()
    return STORE.create(options, internal=True)


def checkout_status(job_id: str) -> dict[str, Any] | None:
    job = STORE.get(job_id, public=False)
    if not job:
        return None
    raw = job.get("result") if isinstance(job.get("result"), dict) else {}
    link = next(
        (
            str(raw.get(key) or "")
            for key in (
                "paypal_link", "provider_redirect_url", "ideal_redirect_url",
                "redirect_url", "checkout_url", "short_link", "url", "link",
            )
            if raw.get(key)
        ),
        "",
    )
    qr_data = next(
        (str(raw.get(key) or "") for key in ("qr_data", "pixPayload", "upi_payload") if raw.get(key)),
        "",
    )
    qr_image = next(
        (
            str(raw.get(key) or "")
            for key in ("qr_image_png", "qr_image_data_url", "pixQrPngUrl", "pixQrSvgUrl")
            if raw.get(key)
        ),
        "",
    )
    result = {
        "plan": str(raw.get("plan") or ""),
        "account_email": str(raw.get("account_email") or raw.get("email") or ""),
        "account_id": str(raw.get("account_id") or ""),
        "provider": str(raw.get("provider") or raw.get("link_type") or ""),
        "link_type": str(raw.get("link_type") or raw.get("provider") or ""),
        "checkout_session_id": str(raw.get("checkout_session_id") or ""),
        "payment_link": link,
        "short_link": str(raw.get("short_link") or ""),
        "verification_url": str(raw.get("verification_url") or ""),
        "checkout_url": str(raw.get("checkout_url") or ""),
        "provider_redirect_url": str(raw.get("provider_redirect_url") or ""),
        "paypal_link": str(raw.get("paypal_link") or raw.get("paypal_url") or ""),
        "qr_data": qr_data,
        "qr_image": qr_image,
        "qr_image_png": str(raw.get("qr_image_png") or ""),
        "qr_image_svg": str(raw.get("qr_image_svg") or ""),
        "country": str(raw.get("checkout_country") or raw.get("country") or ""),
        "currency": str(raw.get("checkout_currency") or raw.get("currency") or ""),
        "checkout_amount": raw.get("checkout_amount"),
        "payment_methods": raw.get("payment_methods") or raw.get("custom_payment_methods") or [],
        "promo_requested": raw.get("promo_requested"),
        "promo_applied": raw.get("promo_applied"),
        "promo_campaign_used": str(raw.get("promo_campaign_used") or raw.get("promo_campaign") or ""),
        "expires_at": raw.get("expires_at"),
    }
    return {
        "status": str(job.get("status") or "queued"),
        "progress": int(job.get("percent") or 0),
        "message": str(job.get("text") or ""),
        "error": _safe_error(job.get("error")),
        "logs": [
            {
                "sequence": int(item.get("sequence") or sequence),
                "time": str(item.get("time") or ""),
                "message": _safe_error(item.get("message")),
            }
            for sequence, item in enumerate(job.get("logs") or [], start=1)
            if isinstance(item, dict)
        ][-200:],
        "result": result,
    }


def cancel_checkout(job_id: str) -> bool:
    return STORE.cancel(job_id)
