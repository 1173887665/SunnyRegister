"""Independent MoMo wallet protocol boundary.

The manager owns orchestration while this module owns the wire contract. A
real deployment points ``api_base_url`` at an adapter that implements the
documented JSON routes below. The adapter can translate those routes to the
official MoMo SDK/API without leaking provider-specific code into the rest of
SunnyRegister.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from typing import Any

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover - worker requirements include httpx
    httpx = None

from .momo_models import MomoProfile, MomoQrOrder, ProviderResult


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "ok", "success"}


class MobileWalletProvider(ABC):
    """Small provider interface used by the MoMo task state machine."""

    @abstractmethod
    def start_register(self, phone: str, proxy: str = "", login_existing: bool = False) -> ProviderResult: ...

    @abstractmethod
    def send_otp(self, session: dict[str, Any]) -> ProviderResult: ...

    @abstractmethod
    def verify_otp(self, session: dict[str, Any], code: str) -> ProviderResult: ...

    @abstractmethod
    def submit_profile(self, session: dict[str, Any], profile: MomoProfile) -> ProviderResult: ...

    @abstractmethod
    def set_pin(self, session: dict[str, Any], pin: str) -> ProviderResult: ...

    @abstractmethod
    def login(self, phone: str, pin: str = "", proxy: str = "", session: dict[str, Any] | None = None) -> ProviderResult: ...

    @abstractmethod
    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult: ...

    def submit_payment_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return ProviderResult(False, {}, "MoMo adapter 未实现支付 OTP 接口")

    def confirm_payment(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(False, {}, "MoMo adapter 未实现支付确认接口")

    def execute(self, operation: str, payload: dict[str, Any]) -> ProviderResult:
        """Dispatch one independently retryable operation.

        ``session`` is part of the payload so the manager can persist and
        resume an adapter session even though each step gets a fresh provider.
        """
        operation = str(operation or "").strip().lower().strip("/")
        session = _as_dict(payload.get("session"))
        if operation in {"register_start", "register/start"}:
            return self.start_register(
                str(payload.get("phone") or ""),
                str(payload.get("proxy") or ""),
                _as_bool(payload.get("login_existing"), False),
            )
        if operation in {"register_send_otp", "register/send-otp"}:
            return self.send_otp(session or {"phone": str(payload.get("phone") or "")})
        if operation in {"register_verify_otp", "register/verify-otp"}:
            return self.verify_otp(session, str(payload.get("otp") or payload.get("code") or ""))
        if operation in {"register_profile", "register/profile"}:
            profile_data = _as_dict(payload.get("profile"))
            profile = MomoProfile(
                phone=str(profile_data.get("phone") or payload.get("phone") or session.get("phone") or ""),
                display_name=str(profile_data.get("display_name") or ""),
                email=str(profile_data.get("email") or ""),
                date_of_birth=str(profile_data.get("date_of_birth") or ""),
                address=str(profile_data.get("address") or ""),
                country=str(profile_data.get("country") or "VN"),
                skip_kyc=_as_bool(profile_data.get("skip_kyc", payload.get("skip_kyc", True)), True),
            )
            return self.submit_profile(session, profile)
        if operation in {"register_pin", "register/pin"}:
            return self.set_pin(session, str(payload.get("pin") or ""))
        if operation == "register":
            checked = self.verify_otp(session, str(payload.get("otp") or ""))
            if not checked.ok:
                return checked
            merged = {**session, **checked.data}
            profile = MomoProfile(
                phone=str(payload.get("phone") or merged.get("phone") or ""),
                display_name=str(payload.get("display_name") or ""),
                email=str(payload.get("email") or ""),
                date_of_birth=str(payload.get("date_of_birth") or ""),
                address=str(payload.get("address") or ""),
                country=str(payload.get("country") or "VN"),
                skip_kyc=_as_bool(payload.get("skip_kyc", True), True),
            )
            submitted = self.submit_profile(merged, profile)
            if not submitted.ok:
                return submitted
            return self.set_pin({**merged, **submitted.data}, str(payload.get("pin") or ""))
        if operation == "login":
            return self.login(
                str(payload.get("phone") or ""),
                str(payload.get("pin") or ""),
                str(payload.get("proxy") or ""),
                session or None,
            )
        if operation in {"payment", "scan_pay", "payment/scan"}:
            order_data = _as_dict(payload.get("order"))
            order = MomoQrOrder(
                str(payload.get("qr_payload") or order_data.get("payload") or ""),
                str(payload.get("amount") or order_data.get("amount") or ""),
                str(payload.get("currency") or order_data.get("currency") or "VND"),
            )
            return self.scan_pay(session or payload, order)
        if operation in {"payment_otp", "payment/otp", "payment_verify_otp", "submit_payment_otp"}:
            return self.submit_payment_otp(session or payload, str(payload.get("otp") or payload.get("code") or ""))
        if operation in {"payment_confirm", "payment/confirm", "confirm_payment"}:
            return self.confirm_payment(session or payload)
        return ProviderResult(False, {}, f"unsupported MoMo operation: {operation}")


class LocalMomoProvider(MobileWalletProvider):
    """Deterministic provider used only by explicit unit-test injection."""

    def start_register(self, phone: str, proxy: str = "", login_existing: bool = False) -> ProviderResult:
        return ProviderResult(True, {"phone": phone, "proxy": proxy, "mode": "login" if login_existing else "register"})

    def send_otp(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(True, {"otp_sent": True})

    def verify_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return ProviderResult(bool(code), {"otp_verified": bool(code), "phone": session.get("phone", "")}, "OTP is empty" if not code else "")

    def submit_profile(self, session: dict[str, Any], profile: MomoProfile) -> ProviderResult:
        return ProviderResult(True, {"kyc_status": "skipped" if profile.skip_kyc else "pending"})

    def set_pin(self, session: dict[str, Any], pin: str) -> ProviderResult:
        return ProviderResult(True, {"pin_set": bool(pin)})

    def login(self, phone: str, pin: str = "", proxy: str = "", session: dict[str, Any] | None = None) -> ProviderResult:
        return ProviderResult(True, {"phone": phone, "session_ready": True, "proxy": proxy, **_as_dict(session)})

    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult:
        return ProviderResult(bool(order.payload), {"order_accepted": bool(order.payload), "payment_id": f"momo-fixture-{uuid.uuid4().hex[:10]}"}, "QR payload is empty" if not order.payload else "")

    def submit_payment_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return ProviderResult(bool(code), {"otp_verified": bool(code)}, "Payment OTP is empty" if not code else "")

    def confirm_payment(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(True, {"confirmed": True, "payment_id": f"momo-fixture-{uuid.uuid4().hex[:10]}"})


class HttpMomoProvider(MobileWalletProvider):
    """JSON adapter for a configured MoMo integration service.

    Routes are stable and provider-neutral: ``register/start``,
    ``register/send-otp``, ``register/verify-otp``, ``register/profile``,
    ``register/pin``, ``login``, ``payment/scan``, ``payment/otp`` and
    ``payment/confirm``. The adapter service is responsible for the official
    MoMo SDK/device protocol and returns
    ``{"ok": bool, ...}`` JSON.
    """

    def __init__(self, base_url: str, timeout: int = 60, headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = max(5, int(timeout))
        self.headers = {str(key): str(value) for key, value in (headers or {}).items() if str(key).strip() and value is not None}

    def _call(self, operation: str, payload: dict[str, Any], proxy: str = "") -> ProviderResult:
        url = f"{self.base_url}/{operation.strip('/')}"
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self.headers}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            if httpx is not None:
                try:
                    with httpx.Client(timeout=self.timeout, proxy=proxy or None, trust_env=False) as client:
                        response = client.post(url, content=body, headers=headers)
                        try:
                            raw: Any = response.json()
                        except ValueError as exc:
                            return ProviderResult(False, {}, f"MoMo adapter 返回了无效 JSON（HTTP {response.status_code}）: {exc}")
                        data = raw if isinstance(raw, dict) else {"value": raw}
                        if response.status_code >= 400:
                            message = str(data.get("error") or data.get("message") or response.reason_phrase or "请求失败")
                            return ProviderResult(False, data, f"MoMo adapter HTTP {response.status_code}: {message}")
                except Exception:
                    # Some Windows loopback servers terminate a keep-alive
                    # socket while httpx is reading it. Retry the same request
                    # through urllib so a valid adapter response is preserved.
                    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
                    opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
                    )
                    with opener.open(request, timeout=self.timeout) as response:
                        raw = json.loads(response.read().decode("utf-8") or "{}")
            else:
                request = urllib.request.Request(url, data=body, method="POST", headers=headers)
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
                )
                with opener.open(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8") or "{}")
            data = raw if isinstance(raw, dict) else {"value": raw}
            if "ok" not in data:
                return ProviderResult(False, data, "MoMo adapter 响应缺少 ok 字段")
            return ProviderResult(_as_bool(data.get("ok"), False), data, str(data.get("error") or data.get("message") or ""))
        except urllib.error.HTTPError as exc:
            try:
                raw_error: Any = json.loads(exc.read().decode("utf-8", "ignore") or "{}")
            except (OSError, ValueError):
                raw_error = {}
            data = raw_error if isinstance(raw_error, dict) else {"value": raw_error}
            message = str(data.get("error") or data.get("message") or exc.reason or "请求失败")
            return ProviderResult(False, data, f"MoMo adapter HTTP {exc.code}: {message}")
        except (OSError, urllib.error.URLError, ValueError) as exc:
            return ProviderResult(False, {}, str(exc))
        except Exception as exc:  # httpx.HTTPError is optional at import time
            return ProviderResult(False, {}, str(exc))

    @staticmethod
    def _session_proxy(session: dict[str, Any]) -> str:
        return str(session.get("proxy") or "")

    def start_register(self, phone: str, proxy: str = "", login_existing: bool = False) -> ProviderResult:
        return self._call("register/start", {"phone": phone, "proxy": proxy, "login_existing": login_existing}, proxy)

    def send_otp(self, session: dict[str, Any]) -> ProviderResult:
        return self._call("register/send-otp", session, self._session_proxy(session))

    def verify_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return self._call("register/verify-otp", {**session, "code": code}, self._session_proxy(session))

    def submit_profile(self, session: dict[str, Any], profile: MomoProfile) -> ProviderResult:
        return self._call("register/profile", {**session, "profile": profile.__dict__}, self._session_proxy(session))

    def set_pin(self, session: dict[str, Any], pin: str) -> ProviderResult:
        return self._call("register/pin", {**session, "pin": pin}, self._session_proxy(session))

    def login(self, phone: str, pin: str = "", proxy: str = "", session: dict[str, Any] | None = None) -> ProviderResult:
        data = {**_as_dict(session), "phone": phone, "pin": pin, "proxy": proxy}
        return self._call("login", data, proxy or self._session_proxy(data))

    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult:
        data = {**session, "order": order.__dict__}
        return self._call("payment/scan", data, self._session_proxy(data))

    def submit_payment_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return self._call("payment/otp", {**session, "code": code}, self._session_proxy(session))

    def confirm_payment(self, session: dict[str, Any]) -> ProviderResult:
        return self._call("payment/confirm", session, self._session_proxy(session))


def _parse_headers(raw: str | dict[str, Any] | None) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items() if str(key).strip() and value is not None}
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return _parse_headers(parsed if isinstance(parsed, dict) else None)
    except (TypeError, ValueError):
        return {}


def build_provider(*, mock_mode: bool, base_url: str, timeout: int = 60, headers: dict[str, str] | str | None = None) -> MobileWalletProvider:
    if mock_mode:
        return LocalMomoProvider()
    if not base_url:
        raise ValueError("MoMo API Base URL is required when live mode is enabled")
    configured = _parse_headers(headers)
    token = os.getenv("OPAI_MOMO_API_TOKEN", "").strip()
    if token and "Authorization" not in configured:
        configured["Authorization"] = f"Bearer {token}"
    return HttpMomoProvider(base_url, timeout=timeout, headers=configured)
