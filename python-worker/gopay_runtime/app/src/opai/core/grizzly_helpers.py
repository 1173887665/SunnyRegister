"""GrizzlySMS adapter for the embedded GoPay flows."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Collection

import tls_client

DEFAULT_BASE_URL = "https://api.grizzlysms.com/stubs/handler_api.php"


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
                if key.strip().startswith("OPAI_GRIZZLYSMS_") and not os.environ.get(key.strip()):
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            pass
        if path:
            break


def grizzly_config() -> dict[str, str]:
    _load_env()
    return {
        "api_key": str(os.environ.get("OPAI_GRIZZLYSMS_API_KEY") or "").strip(),
        "api_base_url": (os.environ.get("OPAI_GRIZZLYSMS_API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/"),
        "country": (os.environ.get("OPAI_GRIZZLYSMS_COUNTRY") or "0").strip(),
        "service": (os.environ.get("OPAI_GRIZZLYSMS_SERVICE") or "dr").strip(),
        "max_price": (os.environ.get("OPAI_GRIZZLYSMS_MAX_PRICE") or "").strip(),
    }


def grizzly_api_key(value: str = "") -> str:
    return str(value or grizzly_config()["api_key"]).strip()


def _request(action: str, *, timeout: int = 30, **params: Any) -> Any:
    cfg = grizzly_config()
    key = cfg["api_key"]
    if not key:
        raise RuntimeError("GrizzlySMS API key is not configured")
    payload = {"api_key": key, "action": action}
    payload.update({k: v for k, v in params.items() if v is not None and v != ""})
    session = tls_client.Session(client_identifier="chrome_120")
    response = session.get(cfg["api_base_url"], params=payload, timeout_seconds=timeout)
    text = str(getattr(response, "text", "") or "").strip()
    if int(getattr(response, "status_code", 200) or 200) >= 400:
        raise RuntimeError(f"GrizzlySMS HTTP error: {text[:300]}")
    try:
        return response.json()
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            return text


def grizzly_balance() -> Any:
    return _request("getBalance")


def grizzly_get_number() -> tuple[str | None, str | None]:
    cfg = grizzly_config()
    params: dict[str, Any] = {"service": cfg["service"], "country": cfg["country"]}
    if cfg["max_price"]:
        params["maxPrice"] = cfg["max_price"]
    body = _request("getNumberV2", timeout=45, **params)
    data = body.get("data", body) if isinstance(body, dict) else {}
    aid = str(data.get("activationId") or data.get("activation_id") or data.get("id") or "").strip()
    phone = str(data.get("phoneNumber") or data.get("phone") or data.get("number") or "").strip()
    if phone and not phone.startswith("+"):
        phone = "+" + phone
    if not aid or not phone:
        raise RuntimeError(str(body))
    return phone, aid


def grizzly_check(activation_id: str) -> Any:
    return _request("getStatusV2", timeout=20, id=activation_id)


def grizzly_wait_code(activation_id: str, timeout: int = 180, *, ignore_code_hashes: Collection[str] | None = None) -> str | None:
    from .sms_helpers import sms_code_sha256
    ignored = {str(x).lower() for x in (ignore_code_hashes or ())}
    deadline = time.time() + max(1, timeout)
    while time.time() < deadline:
        body = grizzly_check(activation_id)
        data = body.get("data", body) if isinstance(body, dict) else {}
        sms = body.get("sms") if isinstance(body, dict) else None
        if isinstance(sms, dict):
            data = {**data, **sms}
        code = str(data.get("code") or data.get("verificationCode") or "").strip()
        if not code:
            match = re.search(r"\b(\d{4,8})\b", str(data.get("text") or data.get("message") or ""))
            code = match.group(1) if match else ""
        if code and sms_code_sha256(code).lower() not in ignored:
            return code
        status = str((body.get("status") if isinstance(body, dict) else body) or "").upper()
        if status in {"STATUS_CANCEL", "NO_ACTIVATION", "BAD_KEY", "ERROR", "CANCELLED"}:
            return None
        time.sleep(min(3, max(0.1, deadline - time.time())))
    return None


def grizzly_resend(activation_id: str) -> bool:
    try:
        _request("setStatus", id=activation_id, status=3)
        return True
    except Exception:
        return False


def grizzly_cancel(activation_id: str) -> bool:
    try:
        _request("setStatus", id=activation_id, status=8)
        return True
    except Exception:
        return False


def grizzly_finish(activation_id: str) -> bool:
    try:
        _request("setStatus", id=activation_id, status=6)
        return True
    except Exception:
        return False
