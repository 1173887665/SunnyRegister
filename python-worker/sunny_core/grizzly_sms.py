from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests


GRIZZLYSMS_DEFAULT_BASE_URL = "https://api.grizzlysms.com/stubs/handler_api.php"
GRIZZLYSMS_DEFAULT_SERVICE = "dr"
GRIZZLYSMS_DEFAULT_COUNTRY = "0"


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any, default: float = -1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _phone(value: Any) -> str:
    text = _clean(value)
    return text if not text or text.startswith("+") else "+" + text


def _body_data(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        data = body.get("data")
        return data if isinstance(data, dict) else body
    return {}


@dataclass(slots=True)
class GrizzlySMSActivation:
    activation_id: str
    number: str
    country_code: str = ""
    raw: dict[str, Any] | None = None


# Short name used by provider adapters and kept for parity with the other SMS
# clients (``SMSPoolActivation``, ``FireFoxActivation``).
GrizzlyActivation = GrizzlySMSActivation


class GrizzlySMSClient:
    """Client for GrizzlySMS handler_api V2 JSON endpoints."""

    def __init__(self, config: dict[str, Any], proxies: dict[str, str] | None = None):
        self.base_url = _clean(config.get("grizzlysms_base_url"), GRIZZLYSMS_DEFAULT_BASE_URL)
        self.api_key = _clean(config.get("grizzlysms_api_key"))
        self.service = _clean(config.get("grizzlysms_default_service") or config.get("sms_service"), GRIZZLYSMS_DEFAULT_SERVICE)
        self.country = _clean(config.get("grizzlysms_default_country") or config.get("sms_country"), GRIZZLYSMS_DEFAULT_COUNTRY)
        self.max_price = _float(config.get("grizzlysms_max_price"), -1)
        self.poll_interval = max(0.1, _float(config.get("grizzlysms_poll_interval"), 5))
        self.proxies = proxies or None
        if not self.api_key:
            raise RuntimeError("GrizzlySMS API Key 未配置")

    def _request(self, action: str, timeout: int = 30, **params: Any) -> Any:
        payload = {"api_key": self.api_key, "action": action}
        payload.update({k: v for k, v in params.items() if v is not None and v != ""})
        response = requests.get(self.base_url, params=payload, timeout=timeout, proxies=self.proxies)
        response.raise_for_status()
        text = str(getattr(response, "text", "") or "").strip()
        try:
            return response.json()
        except ValueError:
            if not text:
                raise RuntimeError("GrizzlySMS 返回为空")
            return text

    def balance(self) -> str:
        body = self._request("getBalance")
        if isinstance(body, dict):
            for key in ("balance", "apiBalance", "money"):
                if body.get(key) is not None:
                    return str(body[key])
            data = _body_data(body)
            if data is not body and data.get("balance") is not None:
                return str(data["balance"])
        text = str(body)
        if ":" in text:
            return text.split(":", 1)[-1].strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return text
        raise RuntimeError(text)

    def get_number(self) -> GrizzlySMSActivation:
        params: dict[str, Any] = {"service": self.service, "country": self.country}
        if self.max_price >= 0:
            params["maxPrice"] = self.max_price
        body = self._request("getNumberV2", timeout=45, **params)
        data = _body_data(body)
        activation_id = _clean(data.get("activationId") or data.get("activation_id") or data.get("id"))
        number = _phone(data.get("phoneNumber") or data.get("phone") or data.get("number"))
        if not activation_id or not number:
            raise RuntimeError(str(body))
        return GrizzlySMSActivation(activation_id, number, _clean(data.get("countryCode")), data)

    def set_status(self, activation_id: str, status: int) -> Any:
        return self._request("setStatus", id=activation_id, status=status)

    def get_status(self, activation_id: str) -> Any:
        return self._request("getStatusV2", timeout=20, id=activation_id)

    def finish(self, activation_id: str) -> None:
        if activation_id:
            self.set_status(activation_id, 6)

    def cancel(self, activation_id: str) -> None:
        if activation_id:
            self.set_status(activation_id, 8)

    def wait_code(
        self,
        activation_id: str,
        timeout: int = 180,
        log: Callable[[str], None] | None = None,
    ) -> str:
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            body = self.get_status(activation_id)
            last = body
            data = _body_data(body)
            sms = body.get("sms") if isinstance(body, dict) else None
            if isinstance(sms, dict):
                data = {**data, **sms}
            code = _clean(data.get("code") or data.get("verificationCode"))
            if not code:
                match = re.search(r"\b(\d{4,8})\b", _clean(data.get("text") or data.get("message")))
                code = match.group(1) if match else ""
            if code:
                if log:
                    log(f"GrizzlySMS received a {len(code)}-digit code (redacted)")
                return code
            status = str((body.get("status") if isinstance(body, dict) else body) or "").upper()
            if status in {
                "STATUS_CANCEL",
                "NO_ACTIVATION",
                "BAD_KEY",
                "ERROR_SQL",
                "BAD_SERVICE",
                "BAD_STATUS",
                "CANCELLED",
                "ERROR",
                "CANCEL",
                "NO_KEY",
            }:
                raise RuntimeError(str(body))
            if log:
                log("GrizzlySMS waiting for code")
            time.sleep(min(self.poll_interval, max(0.1, deadline - time.time())))
        raise TimeoutError(f"GrizzlySMS code timeout: {last or 'no status'}")
