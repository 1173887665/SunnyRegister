from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests


SMSPOOL_DEFAULT_BASE_URL = "https://api.smspool.net"
SMSPOOL_CODE_TIMEOUT_SECONDS = 30
SMSPOOL_POLL_INTERVAL_SECONDS = 3


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _as_float(value: Any, default: float = -1) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _join_phone(cc: str, number: str) -> str:
    cc = cc.strip().lstrip("+")
    number = number.strip().lstrip("+")
    if not number:
        return ""
    return f"+{cc}{number}" if cc and not number.startswith(cc) else f"+{number}"


@dataclass(slots=True)
class SMSPoolActivation:
    order_id: str
    number: str
    token: str = ""


@dataclass(slots=True)
class SMSPoolReusableOrder:
    id: int
    number: str
    order_id: str = ""


class SMSPoolClient:
    """SMSPool client rewritten for SunnyRegister.

    API reference: https://api.smspool.net/resources/postman.json
    Core flow:
    - POST /request/balance
    - POST /request/orders_new to select the most recent reusable number
    - POST /purchase/sms with country/service/pool/max_price/phonenumber
    - POST /sms/check
    - POST /sms/cancel
    """

    def __init__(self, config: dict[str, Any], proxies: dict[str, str] | None = None):
        self.base_url = _clean(config.get("smspool_base_url"), SMSPOOL_DEFAULT_BASE_URL).rstrip("/")
        self.api_key = _clean(config.get("smspool_api_key"))
        self.country = _clean(config.get("smspool_default_country"), "1")
        self.service = _clean(config.get("smspool_default_service"), "671")
        self.pool = _clean(config.get("smspool_pool") or config.get("smspool_default_pool"), "")
        self.max_price = _as_float(config.get("smspool_max_price"), -1)
        self.proxies = proxies or None
        if not self.api_key:
            raise RuntimeError("SMSPool API Key 未配置")

    def _post(self, path: str, data: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        payload = {"key": self.api_key, **{k: v for k, v in data.items() if v is not None and v != ""}}
        resp = requests.post(
            self.base_url + path,
            data=payload,
            headers={"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
            proxies=self.proxies,
        )
        text = resp.text.strip()
        if resp.status_code >= 400:
            raise RuntimeError(f"SMSPool HTTP {resp.status_code}: {text[:500]}")
        try:
            body = resp.json()
        except Exception as exc:
            raise RuntimeError(f"SMSPool 返回非 JSON: {text[:500]}") from exc
        if isinstance(body, dict) and body.get("success") == 0:
            message = str(body.get("message") or body.get("type") or "").strip()
            errors = body.get("errors")
            if not message and isinstance(errors, list) and errors:
                first = errors[0] if isinstance(errors[0], dict) else {}
                message = str(first.get("message") or first.get("param") or "").strip()
            raise RuntimeError(message or text[:500])
        return body if isinstance(body, dict) else {"items": body}

    def balance(self) -> str:
        body = self._post("/request/balance", {})
        return str(body.get("balance") or "").strip()

    def latest_reusable_order(self) -> SMSPoolReusableOrder | None:
        body = self._post("/request/orders_new", {}, timeout=20)
        items: Any = body.get("orders_new")
        if not isinstance(items, list):
            items = body.get("items")
        if not isinstance(items, list):
            data = body.get("data")
            items = data.get("orders_new") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return None

        candidates: list[SMSPoolReusableOrder] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                item_id = int(str(item.get("id") or item.get("ID") or "").strip())
            except (TypeError, ValueError):
                continue
            phone = _join_phone(
                str(item.get("cc") or item.get("country_code") or ""),
                str(item.get("phonenumber") or item.get("phone_number") or item.get("number") or item.get("phone") or ""),
            )
            if not phone:
                continue
            candidates.append(
                SMSPoolReusableOrder(
                    id=item_id,
                    number=phone,
                    order_id=str(item.get("order_id") or item.get("orderid") or item.get("order_code") or "").strip(),
                )
            )
        return max(candidates, key=lambda item: item.id) if candidates else None

    def get_number(self, preferred_number: str = "") -> SMSPoolActivation:
        data: dict[str, Any] = {
            "country": self.country,
            "service": self.service,
            "quantity": "1",
            "activation_type": "SMS",
            "create_token": "0",
        }
        if self.pool:
            data["pool"] = self.pool
        if self.max_price > 0:
            data["max_price"] = f"{self.max_price:.4f}"
        if preferred_number:
            data["phonenumber"] = preferred_number.lstrip("+")
        body = self._post("/purchase/sms", data, timeout=45)
        order_id = str(body.get("order_id") or body.get("orderid") or "").strip()
        if int(body.get("success") or 0) != 1 or not order_id:
            raise RuntimeError(str(body.get("message") or body))
        phone = _join_phone(
            str(body.get("cc") or body.get("country_code") or ""),
            str(body.get("phonenumber") or body.get("number") or body.get("phone") or preferred_number or ""),
        )
        return SMSPoolActivation(order_id=order_id, number=phone, token=str(body.get("token") or ""))

    def check_sms(self, order_id: str) -> dict[str, Any]:
        return self._post("/sms/check", {"orderid": order_id}, timeout=20)

    def cancel(self, order_id: str, attempts: int = 3, retry_delay: float = 2) -> None:
        if not order_id:
            return
        last_error: Exception | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                self._post("/sms/cancel", {"orderid": order_id}, timeout=20)
                return
            except Exception as exc:
                last_error = exc
                locked = "cannot be cancelled yet" in str(exc).lower()
                if not locked or attempt >= attempts:
                    raise
                time.sleep(max(0, retry_delay))
        if last_error:
            raise last_error

    def wait_code(
        self,
        order_id: str,
        timeout: int = SMSPOOL_CODE_TIMEOUT_SECONDS,
        log: Callable[[str], None] | None = None,
    ) -> str:
        deadline = time.time() + timeout
        last_status = ""
        while time.time() < deadline:
            body = self.check_sms(order_id)
            status = int(body.get("status") or 0)
            last_status = str(body)
            if status == 3:
                code = str(body.get("sms") or "").strip()
                if not code:
                    match = re.search(r"\b(\d{4,8})\b", str(body.get("full_sms") or ""))
                    code = match.group(1) if match else ""
                if code:
                    if log:
                        log(f"SMSPool received a {len(code)}-digit code (redacted)")
                    return code
            if status == 6:
                raise RuntimeError(str(body.get("message") or "SMSPool order cancelled"))
            if log:
                local_left = max(0, int(deadline - time.time()))
                log(f"SMSPool waiting for code, local timeout {local_left}s")
            time.sleep(min(SMSPOOL_POLL_INTERVAL_SECONDS, max(0, deadline - time.time())))
        raise TimeoutError(f"SMSPool code timeout: {last_status or 'no status'}")
