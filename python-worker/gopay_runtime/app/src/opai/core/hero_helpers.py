"""HeroSMS REST adapter for the embedded GoPay flows."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Collection

import tls_client

DEFAULT_BASE_URL = "https://hero-sms.com/api/v1"


def _load_env() -> None:
    path = (os.environ.get("OPAI_GOPAY_SMS_ENV_FILE") or "").strip()
    candidates = [Path(path)] if path else [Path.cwd() / "config" / "sms.env"]
    for item in candidates:
        if not item.is_file():
            continue
        try:
            for raw in item.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip().startswith("OPAI_HERO_SMS_") and not os.environ.get(key.strip()):
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            pass
        if path:
            break


def hero_config() -> dict[str, str]:
    _load_env()
    return {
        "api_key": str(os.environ.get("OPAI_HERO_SMS_API_KEY") or "").strip(),
        "api_base_url": (os.environ.get("OPAI_HERO_SMS_API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/"),
        "country": (os.environ.get("OPAI_HERO_SMS_COUNTRY") or "6").strip(),
        "service": (os.environ.get("OPAI_HERO_SMS_SERVICE") or "dr").strip(),
        "max_price": (os.environ.get("OPAI_HERO_SMS_MAX_PRICE") or "").strip(),
    }


def hero_api_key(value: str = "") -> str:
    return str(value or hero_config()["api_key"]).strip()


def _request(method: str, path: str, *, timeout: int = 30, **kwargs: Any) -> Any:
    cfg = hero_config()
    key = cfg["api_key"]
    if not key:
        raise RuntimeError("HeroSMS API key is not configured")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"ApiKey {key}"}
    session = tls_client.Session(client_identifier="chrome_120")
    url = cfg["api_base_url"] + path
    method = method.upper()
    if method == "GET":
        response = session.get(url, headers=headers, timeout_seconds=timeout, **kwargs)
    elif method == "POST":
        response = session.post(url, headers=headers, timeout_seconds=timeout, **kwargs)
    elif method == "DELETE":
        response = session.delete(url, headers=headers, timeout_seconds=timeout, **kwargs)
    else:
        raise ValueError(f"Unsupported HeroSMS method: {method}")
    text = str(getattr(response, "text", "") or "").strip()
    if int(getattr(response, "status_code", 200) or 200) >= 400:
        raise RuntimeError(f"HeroSMS HTTP error: {text[:300]}")
    try:
        return response.json()
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            return text


def hero_balance() -> Any:
    return _request("GET", "/activations/stats")


def hero_get_number() -> tuple[str | None, str | None]:
    cfg = hero_config()
    country: Any = int(cfg["country"]) if cfg["country"].isdigit() else cfg["country"]
    payload: dict[str, Any] = {"service": cfg["service"], "country": country, "amount": 1, "duration": 24, "verificationType": "sms"}
    if cfg["max_price"]:
        payload["maxPrice"] = float(cfg["max_price"])
        payload["fixedPrice"] = True
    body = _request("POST", "/activations", timeout=45, json=payload)
    data = body.get("data", body) if isinstance(body, dict) else body
    records = data if isinstance(data, list) else [data]
    for record in records:
        if not isinstance(record, dict):
            continue
        aid = str(record.get("id") or record.get("activationId") or record.get("activation_id") or "").strip()
        phone = str(record.get("phoneNumber") or record.get("phone") or record.get("number") or record.get("phone_number") or "").strip()
        if phone and not phone.startswith("+"):
            phone = "+" + phone
        if aid and phone:
            return phone, aid
    raise RuntimeError(str(body))


def hero_wait_code(activation_id: str, timeout: int = 180, *, ignore_code_hashes: Collection[str] | None = None) -> str | None:
    from .sms_helpers import sms_code_sha256
    ignored = {str(x).lower() for x in (ignore_code_hashes or ())}
    deadline = time.time() + max(1, timeout)
    while time.time() < deadline:
        body = _request("GET", f"/activations/{activation_id}/otp/last", timeout=20)
        data = body.get("data", body) if isinstance(body, dict) else {}
        if isinstance(data, list):
            data = data[0] if data and isinstance(data[0], dict) else {}
        if not isinstance(data, dict):
            data = {}
        sms = data.get("sms")
        if isinstance(sms, dict):
            data = {**data, **sms}
        code = str(data.get("code") or data.get("verificationCode") or data.get("smsCode") or "").strip()
        if not code:
            match = re.search(r"\b(\d{4,8})\b", str(data.get("text") or data.get("message") or data.get("fullSms") or ""))
            code = match.group(1) if match else ""
        if code and sms_code_sha256(code).lower() not in ignored:
            return code
        status = str(data.get("status") or data.get("state") or "").lower()
        if status in {"cancelled", "canceled", "cancel", "failed", "error"}:
            return None
        time.sleep(min(3, max(0.1, deadline - time.time())))
    return None


def hero_resend(activation_id: str) -> bool:
    return bool(activation_id)


def hero_cancel(activation_id: str) -> bool:
    try:
        _request("DELETE", f"/activations/{activation_id}", timeout=20)
        return True
    except Exception:
        return False


def hero_finish(activation_id: str) -> bool:
    try:
        _request("POST", f"/activations/{activation_id}/finish", timeout=20)
        return True
    except Exception:
        return False
