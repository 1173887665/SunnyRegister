from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests


SMSBOWER_DEFAULT_BASE_URL = "https://smsbower.page/stubs/handler_api.php"
SMSBOWER_DEFAULT_SERVICE = "dr"
SMSBOWER_DEFAULT_COUNTRY = "187"


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _as_float(value: Any, default: float = -1) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normal_phone(number: str) -> str:
    number = number.strip()
    if not number:
        return number
    return number if number.startswith("+") else f"+{number}"


@dataclass(slots=True)
class SMSBowerActivation:
    activation_id: str
    number: str


class SMSBowerClient:
    """Minimal SMSBower client rewritten for SunnyRegister.

    SMSBower uses the SMS-Activate/HeroSMS style handler_api.php protocol:
    getNumber -> ACCESS_NUMBER:activation_id:phone
    getStatus -> STATUS_WAIT_CODE / STATUS_OK:code
    setStatus -> ACCESS_READY / ACCESS_CANCEL / ACCESS_ACTIVATION
    """

    def __init__(self, config: dict[str, Any], proxies: dict[str, str] | None = None):
        self.base_url = _clean(config.get("smsbower_base_url"), SMSBOWER_DEFAULT_BASE_URL)
        self.api_key = _clean(config.get("smsbower_api_key"))
        self.service = _clean(config.get("smsbower_default_service") or config.get("sms_service"), SMSBOWER_DEFAULT_SERVICE)
        self.country = _clean(config.get("smsbower_default_country") or config.get("sms_country"), SMSBOWER_DEFAULT_COUNTRY)
        self.max_price = _as_float(config.get("smsbower_max_price"), -1)
        self.proxies = proxies or None
        if not self.api_key:
            raise RuntimeError("SMSBower API Key 未配置")

    def _request(self, action: str, timeout: int = 30, **params: Any) -> str:
        payload = {"api_key": self.api_key, "action": action, **{k: v for k, v in params.items() if v is not None and v != ""}}
        resp = requests.get(self.base_url, params=payload, timeout=timeout, proxies=self.proxies)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text:
            raise RuntimeError("SMSBower 返回为空")
        return text

    def balance(self) -> str:
        raw = self._request("getBalance")
        if raw.startswith("ACCESS_BALANCE:"):
            return raw.split(":", 1)[1]
        raise RuntimeError(raw)

    def get_number(self) -> SMSBowerActivation:
        params: dict[str, Any] = {"service": self.service, "country": self.country}
        if self.max_price >= 0:
            params["maxPrice"] = self.max_price
        raw = self._request("getNumber", timeout=45, **params)
        if raw.startswith("ACCESS_NUMBER:"):
            parts = raw.split(":")
            if len(parts) >= 3:
                return SMSBowerActivation(activation_id=parts[1], number=_normal_phone(parts[2]))
        raise RuntimeError(raw)

    def set_status(self, activation_id: str, status: int) -> str:
        return self._request("setStatus", id=activation_id, status=status)

    def finish(self, activation_id: str) -> None:
        if activation_id:
            self.set_status(activation_id, 6)

    def cancel(self, activation_id: str) -> None:
        if activation_id:
            self.set_status(activation_id, 8)

    def wait_code(self, activation_id: str, timeout: int = 180, log: Callable[[str], None] | None = None) -> str:
        deadline = time.time() + timeout
        last_status = ""
        while time.time() < deadline:
            raw = self._request("getStatus", timeout=20, id=activation_id)
            last_status = raw
            if raw.startswith("STATUS_OK:"):
                code = raw.split(":", 1)[1].strip()
                if code:
                    if log:
                        log(f"SMSBower received code {code}")
                    return code
            if raw in {"STATUS_WAIT_CODE", "STATUS_WAIT_RETRY", "STATUS_WAIT_RESEND"}:
                if log:
                    log("SMSBower waiting for code")
                time.sleep(5)
                continue
            # Some providers append text around the code; keep a defensive extractor.
            match = re.search(r"\b(\d{4,8})\b", raw)
            if match and "STATUS_" not in raw:
                return match.group(1)
            if raw in {"STATUS_CANCEL", "NO_ACTIVATION", "BAD_KEY", "ERROR_SQL"}:
                raise RuntimeError(raw)
            time.sleep(5)
        raise TimeoutError(f"SMSBower code timeout: {last_status or 'no status'}")

