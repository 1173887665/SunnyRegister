from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests


HERO_SMS_DEFAULT_BASE_URL = "https://hero-sms.com/api/v1"
HERO_SMS_DEFAULT_SERVICE = "dr"
HERO_SMS_DEFAULT_COUNTRY = "0"


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any, default: float = -1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unwrap(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body and body["data"] is not None:
        return body["data"]
    return body


def _phone(value: Any) -> str:
    text = _clean(value)
    return text if not text or text.startswith("+") else "+" + text


@dataclass(slots=True)
class HeroSMSActivation:
    activation_id: str
    number: str
    raw: dict[str, Any] | None = None


class HeroSMSClient:
    """Hero-SMS REST API client.

    The API uses ``Authorization: ApiKey <token>`` and JSON under ``/api/v1``.
    Paths are configurable to keep deployments and test doubles compatible.
    """

    def __init__(self, config: dict[str, Any], proxies: dict[str, str] | None = None):
        self.base_url = _clean(config.get("hero_sms_base_url"), HERO_SMS_DEFAULT_BASE_URL).rstrip("/")
        self.api_key = _clean(config.get("hero_sms_api_key"))
        self.service = _clean(config.get("hero_sms_default_service") or config.get("sms_service"), HERO_SMS_DEFAULT_SERVICE)
        self.country = _clean(config.get("hero_sms_default_country") or config.get("sms_country"), HERO_SMS_DEFAULT_COUNTRY)
        self.operator = _clean(config.get("hero_sms_operator"))
        self.max_price = _float(config.get("hero_sms_max_price"), -1)
        self.poll_interval = max(0.1, _float(config.get("hero_sms_poll_interval"), 5))
        self.balance_path = _clean(config.get("hero_sms_balance_path"), "/activations/stats")
        self.proxies = proxies or None
        if not self.api_key:
            raise RuntimeError("Hero-SMS API Key 未配置")

    def _request(self, method: str, path: str, timeout: int = 30, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"ApiKey {self.api_key}"}
        response = requests.request(method, self.base_url + path, headers=headers, timeout=timeout, proxies=self.proxies, **kwargs)
        response.raise_for_status()
        text = str(getattr(response, "text", "") or "").strip()
        try:
            return response.json()
        except ValueError:
            if not text:
                raise RuntimeError("Hero-SMS 返回为空")
            return text

    def balance(self) -> str:
        body = _unwrap(self._request("GET", self.balance_path))
        if isinstance(body, dict):
            for key in ("balance", "amount", "money", "credit"):
                if body.get(key) is not None:
                    return str(body[key])
            user = body.get("user")
            if isinstance(user, dict) and user.get("balance") is not None:
                return str(user["balance"])
        text = str(body)
        match = re.search(r"(?:balance\s*[:=]\s*)?([0-9]+(?:\.[0-9]+)?)", text, re.I)
        if match:
            return match.group(1)
        raise RuntimeError(text)

    def get_number(self) -> HeroSMSActivation:
        payload: dict[str, Any] = {"service": self.service, "country": int(self.country) if self.country.isdigit() else self.country, "amount": 1, "duration": 24, "verificationType": "sms"}
        if self.operator:
            payload["operator"] = self.operator
        if self.max_price >= 0:
            payload["maxPrice"] = self.max_price
            payload["fixedPrice"] = True
        body = self._request("POST", "/activations", timeout=45, json=payload)
        data = _unwrap(body)
        if not isinstance(data, dict):
            raise RuntimeError(str(body))
        activation_id = _clean(data.get("id") or data.get("activationId") or data.get("activation_id"))
        number = _phone(data.get("phoneNumber") or data.get("phone") or data.get("number") or data.get("phone_number"))
        if not activation_id or not number:
            raise RuntimeError(str(body))
        return HeroSMSActivation(activation_id, number, data)

    def _status(self, activation_id: str) -> Any:
        try:
            return self._request("GET", f"/activations/{activation_id}", timeout=20)
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if response is None or response.status_code != 404:
                raise
            return self._request("GET", "/activations", timeout=20, params={"id": activation_id})

    def wait_code(self, activation_id: str, timeout: int = 180, log: Callable[[str], None] | None = None) -> str:
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            body = self._request("GET", f"/activations/{activation_id}/otp/last", timeout=20)
            last = body
            data = _unwrap(body)
            if isinstance(data, list):
                data = next((x for x in data if isinstance(x, dict) and str(x.get("id") or x.get("activationId")) == str(activation_id)), {})
            if not isinstance(data, dict):
                data = {}
            sms = data.get("sms")
            if isinstance(sms, dict):
                data = {**data, **sms}
            code = _clean(data.get("code") or data.get("verificationCode") or data.get("smsCode"))
            if not code:
                match = re.search(r"\b(\d{4,8})\b", _clean(data.get("text") or data.get("message") or data.get("fullSms")))
                code = match.group(1) if match else ""
            if code:
                if log:
                    log(f"Hero-SMS received a {len(code)}-digit code (redacted)")
                return code
            status = _clean(data.get("status") or data.get("state")).lower()
            if status in {"cancelled", "canceled", "cancel", "failed", "error"}:
                raise RuntimeError(str(body))
            if log:
                log("Hero-SMS waiting for code")
            time.sleep(min(self.poll_interval, max(0.1, deadline - time.time())))
        raise TimeoutError(f"Hero-SMS code timeout: {last or 'no status'}")

    def finish(self, activation_id: str) -> None:
        if not activation_id:
            return
        path = f"/activations/{activation_id}/finish"
        self._request("POST", path, timeout=20)

    def cancel(self, activation_id: str) -> None:
        if activation_id:
            self._request("DELETE", f"/activations/{activation_id}", timeout=20)
