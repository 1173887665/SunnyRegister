"""Independent SMS activation adapters for the MoMo runtime.

This module intentionally does not import the GoPay runtime.  Provider details
are kept behind one small lease interface so registration can acquire a phone,
poll an OTP, and close the activation regardless of the selected supplier.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Collection
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover - optional in minimal local installs
    httpx = None


@dataclass
class SmsLease:
    provider: str
    phone: str
    activation_id: str
    api_key: str = ""
    sms_url: str = ""


def code_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def mask_secret(value: str) -> str:
    value = str(value or "").strip()
    if len(value) <= 8:
        return f"{value[:2]}***" if value else ""
    return f"{value[:4]}...{value[-4:]}"


def _json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _values(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _values(child)
    elif value is not None:
        yield str(value)


def _extract_code(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    match = re.search(r"(?<!\d)(\d{4,8})(?!\d)", text)
    return match.group(1) if match else ""


def _request_json(method: str, url: str, *, params: dict[str, Any] | None = None, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 30, proxy: str = "", json_body: bool = False) -> Any:
    method = method.upper()
    request_headers = {"Accept": "application/json", **(headers or {})}
    if httpx is not None:
        with httpx.Client(timeout=timeout, proxy=proxy or None, trust_env=False) as client:
            response = client.request(method, url, params=params if method == "GET" else None, json=data if json_body and method != "GET" else None, data=data if not json_body and method != "GET" else None, headers=request_headers)
            status = int(response.status_code)
            text = response.text.strip()
    else:
        query = f"?{urlencode({key: value for key, value in (params or {}).items() if value is not None and value != ''})}" if params else ""
        body = None
        request_url = url + (query if method == "GET" else "")
        if method != "GET" and data is not None:
            if json_body:
                body = json.dumps({key: value for key, value in data.items() if value is not None and value != ""}, ensure_ascii=False).encode("utf-8")
                request_headers.setdefault("Content-Type", "application/json")
            else:
                body = urlencode({key: value for key, value in data.items() if value is not None and value != ""}).encode("utf-8")
                request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
        response = opener.open(Request(request_url, data=body, method=method, headers=request_headers), timeout=timeout)
        text = response.read().decode("utf-8", "ignore").strip()
        status = int(getattr(response, "status", 200) or 200)
    if status >= 400:
        raise RuntimeError(f"SMS provider HTTP {status}: {text[:240]}")
    return _json(text) if _json(text) is not None else text


class MomoSmsProvider:
    name = "pool"

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.country = str(settings.get("sms_country_code") or "10").strip()
        self.service = str(settings.get("sms_service_code") or "momo").strip()
        self.max_price = str(settings.get("sms_max_price") or "").strip()
        self.api_key = str(settings.get("sms_api_key") or "").strip()
        self.proxy = str(settings.get("sms_proxy") or "").strip()

    def acquire(self) -> SmsLease:
        raise NotImplementedError

    def wait_code(self, lease: SmsLease, timeout: int, ignored: Collection[str] = ()) -> str | None:
        raise NotImplementedError

    def resend(self, lease: SmsLease) -> bool:
        return False

    def finish(self, lease: SmsLease) -> bool:
        return True

    def cancel(self, lease: SmsLease) -> bool:
        return True


class SmsBowerProvider(MomoSmsProvider):
    name = "smsbower"
    default_url = "https://smsbower.page/stubs/handler_api.php"

    def _call(self, action: str, **params: Any) -> Any:
        payload = {"api_key": self.api_key, "action": action, **params}
        return _request_json("GET", str(self.settings.get("sms_api_base_url") or self.default_url).rstrip("/"), params=payload, timeout=45, proxy=self.proxy)

    def acquire(self) -> SmsLease:
        body = self._call("getNumber", service=self.service, country=self.country)
        text = str(body or "")
        if text.startswith("ACCESS_NUMBER:"):
            _, aid, phone = text.split(":", 2)
            return SmsLease(self.name, _normalize_phone(phone), aid, self.api_key)
        values = list(_values(body))
        aid = next((item for item in values if re.fullmatch(r"\d+", item)), "")
        phone = next((item for item in values if len(re.sub(r"\D", "", item)) >= 9 and item != aid), "")
        if not aid or not phone:
            raise RuntimeError(f"SMSBower returned no phone: {str(body)[:240]}")
        return SmsLease(self.name, _normalize_phone(phone), aid, self.api_key)

    def wait_code(self, lease: SmsLease, timeout: int, ignored: Collection[str] = ()) -> str | None:
        ignored_set = {str(item) for item in ignored}
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            body = self._call("getStatus", id=lease.activation_id)
            code = _extract_code(body)
            if code and code_hash(code) not in ignored_set:
                return code
            if "STATUS_CANCEL" in str(body).upper():
                return None
            time.sleep(min(4, max(0.1, deadline - time.time())))
        return None

    def resend(self, lease: SmsLease) -> bool:
        return "ACCESS_RETRY_GET" in str(self._call("setStatus", id=lease.activation_id, status=3)).upper()

    def finish(self, lease: SmsLease) -> bool:
        return "ACCESS_" in str(self._call("setStatus", id=lease.activation_id, status=6)).upper()

    def cancel(self, lease: SmsLease) -> bool:
        return "ACCESS_" in str(self._call("setStatus", id=lease.activation_id, status=8)).upper()


class SmsPoolProvider(MomoSmsProvider):
    name = "smspool"
    default_url = "https://api.smspool.net"

    def _call(self, path: str, **data: Any) -> Any:
        payload = {"key": self.api_key, **data}
        return _request_json("POST", f"{str(self.settings.get('sms_api_base_url') or self.default_url).rstrip('/')}{path}", data=payload, headers={"Authorization": f"Bearer {self.api_key}"}, timeout=45, proxy=self.proxy)

    def acquire(self) -> SmsLease:
        payload: dict[str, Any] = {"country": self.country, "service": self.service, "quantity": 1, "activation_type": "SMS"}
        if self.settings.get("sms_pool"):
            payload["pool"] = self.settings["sms_pool"]
        if self.max_price:
            payload["max_price"] = self.max_price
        body = self._call("/purchase/sms", **payload)
        row = body.get("data", body) if isinstance(body, dict) else {}
        aid = str(row.get("order_id") or row.get("orderid") or row.get("id") or "").strip()
        phone = str(row.get("phonenumber") or row.get("phone") or row.get("number") or "").strip()
        if not aid or not phone:
            raise RuntimeError(f"SMSPool returned no phone: {str(body)[:240]}")
        return SmsLease(self.name, _normalize_phone(phone), aid, self.api_key)

    def wait_code(self, lease: SmsLease, timeout: int, ignored: Collection[str] = ()) -> str | None:
        ignored_set = {str(item) for item in ignored}
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            body = self._call("/sms/check", orderid=lease.activation_id)
            code = _extract_code(body.get("sms") if isinstance(body, dict) else body)
            if code and code_hash(code) not in ignored_set:
                return code
            if isinstance(body, dict) and str(body.get("status") or "") == "6":
                return None
            time.sleep(min(3, max(0.1, deadline - time.time())))
        return None

    def resend(self, lease: SmsLease) -> bool:
        self._call("/sms/resend", orderid=lease.activation_id)
        return True

    def finish(self, lease: SmsLease) -> bool:
        self._call("/sms/activate", orderid=lease.activation_id)
        return True

    def cancel(self, lease: SmsLease) -> bool:
        self._call("/sms/cancel", orderid=lease.activation_id)
        return True


class GrizzlySmsProvider(MomoSmsProvider):
    name = "grizzlysms"
    default_url = "https://api.grizzlysms.com/stubs/handler_api.php"

    def _call(self, action: str, **params: Any) -> Any:
        return _request_json("GET", str(self.settings.get("sms_api_base_url") or self.default_url).rstrip("/"), params={"api_key": self.api_key, "action": action, **params}, timeout=45, proxy=self.proxy)

    def acquire(self) -> SmsLease:
        body = self._call("getNumberV2", service=self.service, country=self.country, maxPrice=self.max_price)
        row = body.get("data", body) if isinstance(body, dict) else {}
        aid = str(row.get("activationId") or row.get("activation_id") or row.get("id") or "").strip()
        phone = str(row.get("phoneNumber") or row.get("phone") or row.get("number") or "").strip()
        if not aid or not phone:
            raise RuntimeError(f"GrizzlySMS returned no phone: {str(body)[:240]}")
        return SmsLease(self.name, _normalize_phone(phone), aid, self.api_key)

    def wait_code(self, lease: SmsLease, timeout: int, ignored: Collection[str] = ()) -> str | None:
        ignored_set = {str(item) for item in ignored}
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            body = self._call("getStatusV2", id=lease.activation_id)
            code = _extract_code(body)
            if code and code_hash(code) not in ignored_set:
                return code
            if any(token in str(body).upper() for token in ("CANCEL", "NO_ACTIVATION", "BAD_KEY")):
                return None
            time.sleep(min(3, max(0.1, deadline - time.time())))
        return None

    def resend(self, lease: SmsLease) -> bool:
        self._call("setStatus", id=lease.activation_id, status=3)
        return True

    def finish(self, lease: SmsLease) -> bool:
        self._call("setStatus", id=lease.activation_id, status=6)
        return True

    def cancel(self, lease: SmsLease) -> bool:
        self._call("setStatus", id=lease.activation_id, status=8)
        return True


class HeroSmsProvider(MomoSmsProvider):
    name = "hero_sms"
    default_url = "https://hero-sms.com/api/v1"

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("json_body", method.upper() not in {"GET", "HEAD"})
        return _request_json(method, f"{str(self.settings.get('sms_api_base_url') or self.default_url).rstrip('/')}{path}", headers={"Authorization": f"ApiKey {self.api_key}"}, timeout=45, proxy=self.proxy, **kwargs)

    def acquire(self) -> SmsLease:
        payload: dict[str, Any] = {"service": self.service, "country": int(self.country) if self.country.isdigit() else self.country, "amount": 1, "duration": 24, "verificationType": "sms"}
        if self.max_price:
            payload.update({"maxPrice": float(self.max_price), "fixedPrice": True})
        body = self._call("POST", "/activations", data=payload)
        row = body.get("data", body) if isinstance(body, dict) else body
        row = row[0] if isinstance(row, list) and row else row
        aid = str(row.get("id") or row.get("activationId") or "").strip() if isinstance(row, dict) else ""
        phone = str(row.get("phoneNumber") or row.get("phone") or row.get("number") or "").strip() if isinstance(row, dict) else ""
        if not aid or not phone:
            raise RuntimeError(f"HeroSMS returned no phone: {str(body)[:240]}")
        return SmsLease(self.name, _normalize_phone(phone), aid, self.api_key)

    def wait_code(self, lease: SmsLease, timeout: int, ignored: Collection[str] = ()) -> str | None:
        ignored_set = {str(item) for item in ignored}
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            body = self._call("GET", f"/activations/{lease.activation_id}/otp/last")
            code = _extract_code(body)
            if code and code_hash(code) not in ignored_set:
                return code
            time.sleep(min(3, max(0.1, deadline - time.time())))
        return None

    def finish(self, lease: SmsLease) -> bool:
        self._call("POST", f"/activations/{lease.activation_id}/finish")
        return True

    def cancel(self, lease: SmsLease) -> bool:
        self._call("DELETE", f"/activations/{lease.activation_id}")
        return True


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("0084"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "84" + digits[1:]
    if not digits.startswith("84") or not 11 <= len(digits) <= 12:
        return ""
    return f"+{digits}"


def build_sms_provider(settings: dict[str, Any]) -> MomoSmsProvider:
    provider = str(settings.get("phone_source") or "pool").strip().lower()
    if provider == "smsbower":
        return SmsBowerProvider(settings)
    if provider == "smspool":
        return SmsPoolProvider(settings)
    if provider == "grizzlysms":
        return GrizzlySmsProvider(settings)
    if provider == "hero_sms":
        return HeroSmsProvider(settings)
    return MomoSmsProvider(settings)
