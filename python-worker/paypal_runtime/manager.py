"""Isolated PayPal agreement task manager.

The protocol implementation is vendored from the working pay153 agreement
project in ``protocol_web.py``.  This adapter owns only SunnyRegister concerns:
configuration, task listing and the HTTP-facing OTP/cancel operations.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from protocol_web import (  # noqa: E402
    JOBS,
    JOBS_LOCK,
    create_job,
    get_job,
    parse_proxy_pool,
    supported_country_codes,
)

CONFIG_PATH = Path(os.getenv("PAYPAL_AGREEMENT_CONFIG_PATH") or (RUNTIME_ROOT / "config" / "paypal_agreement.json"))
CONFIG_LOCK = threading.RLock()
START_LOCK = threading.Lock()
DEFAULT_CONFIG = {
    "country": "BR",
    "buyer_mode": "identity_elevation",
    "proxy_pool": [],
}


def _read_config() -> dict[str, Any]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {**DEFAULT_CONFIG, **payload}
    except (OSError, ValueError, TypeError):
        pass
    return dict(DEFAULT_CONFIG)


def _write_config(payload: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)


def _mask_proxy(value: str) -> str:
    text = str(value or "")
    if "://" in text and "@" in text:
        scheme, rest = text.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    parts = text.split(":", 3)
    return f"{parts[0]}:{parts[1]}:***:***" if len(parts) == 4 else text


def public_config() -> dict[str, Any]:
    with CONFIG_LOCK:
        config = _read_config()
    proxies = [str(item).strip() for item in config.get("proxy_pool") or [] if str(item).strip()]
    return {
        "country": str(config.get("country") or "BR").upper(),
        "buyer_mode": str(config.get("buyer_mode") or "identity_elevation"),
        "proxy_count": len(proxies),
        "proxy_pool_configured": bool(proxies),
        "proxy_preview": [_mask_proxy(item) for item in proxies[:3]],
    }


def update_config(data: dict[str, Any]) -> dict[str, Any]:
    with CONFIG_LOCK:
        config = _read_config()
        country = str(data.get("country") or config.get("country") or "BR").strip().upper()
        allowed = supported_country_codes() or {"BR", "GB", "US", "JP", "TH", "ID", "PH", "TW", "MX", "AE", "AU", "CA"}
        if country not in allowed:
            raise ValueError("PayPal 国家参数不正确")
        buyer_mode = str(data.get("buyer_mode") or config.get("buyer_mode") or "identity_elevation").strip().lower()
        if buyer_mode not in {"original", "identity_elevation"}:
            raise ValueError("Buyer 模式参数不正确")
        raw_proxies = data.get("proxy_pool")
        if raw_proxies is not None and (isinstance(raw_proxies, list) or str(raw_proxies).strip()):
            proxies = parse_proxy_pool(raw_proxies)
            if not proxies:
                raise ValueError("代理池不能为空")
            config["proxy_pool"] = proxies
        config["country"] = country
        config["buyer_mode"] = buyer_mode
        _write_config(config)
    return public_config()


def start(data: dict[str, Any]) -> dict[str, Any]:
    # Serialize allocation so two simultaneous requests cannot both observe a
    # phone as available before either task has been inserted into JOBS.
    with START_LOCK:
        with CONFIG_LOCK:
            config = _read_config()
        country = str(data.get("country") or config.get("country") or "BR").strip().upper()
        buyer_mode = str(data.get("buyer_mode") or config.get("buyer_mode") or "identity_elevation").strip().lower()
        proxies = data.get("proxy_pool")
        if proxies is None or (not isinstance(proxies, list) and not str(proxies).strip()):
            proxies = config.get("proxy_pool") or []
        phone_digits = re.sub(r"\D", "", str(data.get("phone") or ""))
        if phone_digits:
            with JOBS_LOCK:
                duplicate_phone = next(
                    (
                        item for item in JOBS.values()
                        if item.status in {"queued", "running", "awaiting_otp", "awaiting_captcha", "cancelling"}
                        and re.sub(r"\D", "", str(item.phone or "")) == phone_digits
                    ),
                    None,
                )
            if duplicate_phone is not None:
                raise ValueError("这个手机号已有未完成的 PayPal 协议任务，请更换手机号")
        # Every task gets its own owner id, so the source project's per-browser
        # guard cannot serialize unrelated BA/phone pairs.  Its global semaphore
        # still limits total protocol concurrency.
        job = create_job(
            owner_device_id=f"sunny-paypal-{uuid.uuid4().hex}",
            ba_token=str(data.get("ba_token") or data.get("paypal_url") or ""),
            phone=str(data.get("phone") or ""),
            country=country,
            buyer_mode=buyer_mode,
            debug=False,
            max_card_attempts=max(1, min(int(data.get("max_card_attempts") or 5), 20)),
            manual_funding=False,
            agreement_only=bool(data.get("agreement_only")),
            proxy_pool=proxies,
        )
    return job.to_dict(include_logs=False)


def get(job_id: str, *, include_logs: bool = True, log_offset: int = 0) -> dict[str, Any] | None:
    job = get_job(str(job_id or ""))
    return job.to_dict(include_logs=include_logs, log_offset=log_offset) if job else None


def list_jobs() -> list[dict[str, Any]]:
    with JOBS_LOCK:
        jobs = list(JOBS.values())
    jobs.sort(key=lambda item: item.updated_at, reverse=True)
    return [job.to_dict(include_logs=False) for job in jobs]


def submit_otp(job_id: str, value: str) -> dict[str, Any] | None:
    job = get_job(str(job_id or ""))
    if not job:
        return None
    job.submit_input(str(value or ""))
    job.add_log("INFO", "已从支付管理页面提交 PayPal 短信验证码。")
    return job.to_dict(include_logs=False)


def cancel(job_id: str) -> dict[str, Any] | None:
    job = get_job(str(job_id or ""))
    if not job:
        return None
    job.cancel()
    return job.to_dict(include_logs=False)
