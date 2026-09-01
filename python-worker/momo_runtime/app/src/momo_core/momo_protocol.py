"""Direct MoMo wallet protocol boundary.

The manager owns orchestration while this module owns HTTP transport,
authentication, signing, route selection and session propagation.  Live mode
talks to the configured protocol host directly; no sidecar adapter process is
required.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from abc import ABC, abstractmethod
from typing import Any

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover - worker requirements include httpx
    httpx = None

from .momo_models import MomoProfile, MomoQrOrder, ProviderResult


DEFAULT_PROTOCOL_ROUTES = {
    "register_start": "/register/start",
    "register_send_otp": "/register/send-otp",
    "register_verify_otp": "/register/verify-otp",
    "register_profile": "/register/profile",
    "register_pin": "/register/pin",
    "device_bind": "/device/bind",
    "session": "/session",
    "login": "/login",
    "payment_scan": "/payment/scan",
    "payment_otp": "/payment/otp",
    "payment_confirm": "/payment/confirm",
}

_OPERATION_ROUTE_KEYS = {
    "register/start": "register_start",
    "register/send-otp": "register_send_otp",
    "register/verify-otp": "register_verify_otp",
    "register/profile": "register_profile",
    "register/pin": "register_pin",
    "device/bind": "device_bind",
    "session": "session",
    "login": "login",
    "payment/scan": "payment_scan",
    "payment/otp": "payment_otp",
    "payment/confirm": "payment_confirm",
}


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


def _has_header(headers: dict[str, str], name: str) -> bool:
    target = name.lower()
    return any(str(key).lower() == target for key in headers)


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
    def bind_device(self, session: dict[str, Any]) -> ProviderResult: ...

    @abstractmethod
    def get_session(self, session: dict[str, Any]) -> ProviderResult: ...

    @abstractmethod
    def login(self, phone: str, pin: str = "", proxy: str = "", session: dict[str, Any] | None = None) -> ProviderResult: ...

    @abstractmethod
    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult: ...

    def submit_payment_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return ProviderResult(False, {}, "MoMo 协议未实现支付 OTP 接口")

    def confirm_payment(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(False, {}, "MoMo 协议未实现支付确认接口")

    def execute(self, operation: str, payload: dict[str, Any]) -> ProviderResult:
        """Dispatch one independently retryable operation.

        ``session`` is part of the payload so the manager can persist and
        resume a protocol session even though each step gets a fresh provider.
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
        if operation in {"bind_device", "device/bind", "register/bind-device"}:
            return self.bind_device(session or payload)
        if operation in {"get_session", "session", "session/get"}:
            return self.get_session(session or payload)
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

    def bind_device(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(True, {"device_bound": True, "device_id": f"momo-device-{uuid.uuid4().hex[:10]}"})

    def get_session(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(True, {"session_ready": True, **_as_dict(session)})

    def login(self, phone: str, pin: str = "", proxy: str = "", session: dict[str, Any] | None = None) -> ProviderResult:
        return ProviderResult(True, {"phone": phone, "session_ready": True, "proxy": proxy, **_as_dict(session)})

    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult:
        return ProviderResult(bool(order.payload), {"order_accepted": bool(order.payload), "payment_id": f"momo-fixture-{uuid.uuid4().hex[:10]}"}, "QR payload is empty" if not order.payload else "")

    def submit_payment_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return ProviderResult(bool(code), {"otp_verified": bool(code)}, "Payment OTP is empty" if not code else "")

    def confirm_payment(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(True, {"confirmed": True, "payment_id": f"momo-fixture-{uuid.uuid4().hex[:10]}"})


class DirectMomoProvider(MobileWalletProvider):
    """Direct HTTP protocol client used inside the Python Worker.

    Route paths, static headers and authentication are supplied as runtime
    configuration.  Responses may use ``ok``, ``success`` or a zero
    ``resultCode``/``code`` and nested ``data`` objects; the provider
    normalizes those common wire formats for the task state machine.
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 60,
        headers: dict[str, str] | None = None,
        routes: dict[str, str] | None = None,
        auth_mode: str = "none",
        token: str = "",
        access_key: str = "",
        secret_key: str = "",
        signature_header: str = "X-Signature",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = max(5, int(timeout))
        self.headers = _parse_headers(headers)
        self.routes = {**DEFAULT_PROTOCOL_ROUTES, **_parse_routes(routes)}
        mode = str(auth_mode or "none").strip().lower()
        self.auth_mode = mode if mode in {"none", "bearer", "hmac_sha256"} else "none"
        self.token = str(token or "").strip()
        self.access_key = str(access_key or "").strip()
        self.secret_key = str(secret_key or "").strip()
        self.signature_header = str(signature_header or "X-Signature").strip() or "X-Signature"

    def _url(self, operation: str) -> tuple[str, str]:
        route_key = _OPERATION_ROUTE_KEYS.get(operation.strip("/"), operation.strip("/").replace("/", "_"))
        route = str(self.routes.get(route_key) or f"/{operation.strip('/')}").strip()
        if route.startswith(("http://", "https://")):
            return route, urllib.parse.urlsplit(route).path or "/"
        path = f"/{route.lstrip('/')}"
        return f"{self.base_url}{path}", path

    @staticmethod
    def _session_token(payload: dict[str, Any]) -> str:
        session = _as_dict(payload.get("session"))
        for source in (payload, session):
            for key in ("access_token", "session_token", "auth_token", "token"):
                value = str(source.get(key) or "").strip()
                if value:
                    return value
        return ""

    def _request_headers(self, path: str, body: bytes, payload: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self.headers}
        request_id = str(payload.get("idempotency_key") or payload.get("request_id") or uuid.uuid4().hex).strip()
        if not _has_header(headers, "X-Request-Id"):
            headers["X-Request-Id"] = request_id
        if not _has_header(headers, "Idempotency-Key"):
            headers["Idempotency-Key"] = request_id

        session_token = self._session_token(payload)
        bearer = session_token or self.token
        if self.auth_mode == "bearer" and bearer and not _has_header(headers, "Authorization"):
            headers["Authorization"] = f"Bearer {bearer}"
        elif session_token and not _has_header(headers, "Authorization"):
            headers["Authorization"] = f"Bearer {session_token}"

        if self.auth_mode == "hmac_sha256":
            timestamp = str(int(time.time() * 1000))
            nonce = uuid.uuid4().hex
            body_hash = hashlib.sha256(body).hexdigest()
            canonical = f"POST\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
            signature = hmac.new(self.secret_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
            headers.setdefault("X-Access-Key", self.access_key)
            headers.setdefault("X-Timestamp", timestamp)
            headers.setdefault("X-Nonce", nonce)
            headers.setdefault(self.signature_header, signature)

        device_id = str(payload.get("device_id") or _as_dict(payload.get("session")).get("device_id") or "").strip()
        if device_id and not _has_header(headers, "X-Device-Id"):
            headers["X-Device-Id"] = device_id
        return headers

    @staticmethod
    def _normalize_result(raw: Any, status_code: int, reason: str = "") -> tuple[bool, dict[str, Any], str]:
        envelope = raw if isinstance(raw, dict) else {"value": raw}
        nested = envelope.get("data")
        data = {**envelope, **nested} if isinstance(nested, dict) else dict(envelope)
        if "ok" in envelope:
            ok = _as_bool(envelope.get("ok"), False)
        elif "success" in envelope:
            ok = _as_bool(envelope.get("success"), False)
        elif "resultCode" in envelope:
            ok = str(envelope.get("resultCode")) == "0"
        elif "code" in envelope and str(envelope.get("code")).strip().lstrip("-").isdigit():
            ok = str(envelope.get("code")) == "0"
        elif str(envelope.get("status") or "").strip().lower() in {"ok", "success", "succeeded", "completed"}:
            ok = True
        else:
            ok = 200 <= status_code < 300
        message = str(data.get("error") or data.get("message") or data.get("msg") or reason or "")
        return ok and status_code < 400, data, message

    def _call(self, operation: str, payload: dict[str, Any], proxy: str = "") -> ProviderResult:
        url, path = self._url(operation)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers = self._request_headers(path, body, payload)
        try:
            if httpx is not None:
                with httpx.Client(timeout=self.timeout, proxy=proxy or None, trust_env=False) as client:
                    response = client.post(url, content=body, headers=headers)
                    try:
                        raw: Any = response.json()
                    except ValueError as exc:
                        return ProviderResult(False, {}, f"MoMo 协议返回了无效 JSON（HTTP {response.status_code}）: {exc}")
                    ok, data, message = self._normalize_result(raw, response.status_code, response.reason_phrase)
                    if response.status_code >= 400:
                        return ProviderResult(False, data, f"MoMo 协议 HTTP {response.status_code}: {message or '请求失败'}")
            else:
                request = urllib.request.Request(url, data=body, method="POST", headers=headers)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
                with opener.open(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8") or "{}")
                    ok, data, message = self._normalize_result(raw, int(getattr(response, "status", 200) or 200))
            if ok and operation == "login" and not any(data.get(key) for key in ("session", "token", "access_token", "session_id")):
                return ProviderResult(False, data, "MoMo 协议登录响应缺少 session/token")
            if ok and operation == "payment/scan" and not any(data.get(key) for key in ("payment_id", "payment_token", "transaction_id", "transId", "requires_otp", "otp_required", "requires_confirmation", "confirmation_required")):
                return ProviderResult(False, data, "MoMo 协议扫码响应缺少支付标识或下一步状态")
            return ProviderResult(ok, data, "" if ok else message)
        except urllib.error.HTTPError as exc:
            try:
                raw_error: Any = json.loads(exc.read().decode("utf-8", "ignore") or "{}")
            except (OSError, ValueError):
                raw_error = {}
            _ok, data, message = self._normalize_result(raw_error, int(exc.code), str(exc.reason or ""))
            return ProviderResult(False, data, f"MoMo 协议 HTTP {exc.code}: {message or '请求失败'}")
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
        profile_data = dict(profile.__dict__)
        return self._call("register/profile", {**session, **profile_data, "profile": profile_data}, self._session_proxy(session))

    def set_pin(self, session: dict[str, Any], pin: str) -> ProviderResult:
        return self._call("register/pin", {**session, "pin": pin}, self._session_proxy(session))

    def bind_device(self, session: dict[str, Any]) -> ProviderResult:
        return self._call("device/bind", session, self._session_proxy(session))

    def get_session(self, session: dict[str, Any]) -> ProviderResult:
        return self._call("session", session, self._session_proxy(session))

    def login(self, phone: str, pin: str = "", proxy: str = "", session: dict[str, Any] | None = None) -> ProviderResult:
        data = {**_as_dict(session), "phone": phone, "pin": pin, "proxy": proxy}
        return self._call("login", data, proxy or self._session_proxy(data))

    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult:
        order_data = dict(order.__dict__)
        data = {**session, "qr_payload": order.payload, "amount": order.amount, "currency": order.currency, "order": order_data}
        return self._call("payment/scan", data, self._session_proxy(data))

    def submit_payment_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return self._call("payment/otp", {**session, "code": code}, self._session_proxy(session))

    def confirm_payment(self, session: dict[str, Any]) -> ProviderResult:
        return self._call("payment/confirm", session, self._session_proxy(session))

def _parse_headers(raw: str | dict[str, Any] | None) -> dict[str, str]:
    if isinstance(raw, dict):
        return {
            str(key): str(value)
            for key, value in raw.items()
            if str(key).strip() and value is not None and str(key).strip().lower() not in {"host", "content-length"}
        }
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return _parse_headers(parsed if isinstance(parsed, dict) else None)
    except (TypeError, ValueError):
        return {}


def _parse_routes(raw: str | dict[str, Any] | None) -> dict[str, str]:
    if isinstance(raw, dict):
        result: dict[str, str] = {}
        for key, value in raw.items():
            normalized_key = str(key or "").strip().lower().strip("/").replace("/", "_").replace("-", "_")
            route = str(value or "").strip()
            if normalized_key in DEFAULT_PROTOCOL_ROUTES and route:
                result[normalized_key] = route
        return result
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return _parse_routes(parsed if isinstance(parsed, dict) else None)


def build_provider(
    *,
    mock_mode: bool,
    base_url: str,
    timeout: int = 60,
    headers: dict[str, str] | str | None = None,
    routes: dict[str, Any] | str | None = None,
    auth_mode: str | None = None,
    token: str = "",
    access_key: str = "",
    secret_key: str = "",
    signature_header: str = "X-Signature",
) -> MobileWalletProvider:
    if mock_mode:
        return LocalMomoProvider()
    if not base_url:
        raise ValueError("MoMo 协议 Base URL is required when live mode is enabled")
    configured = _parse_headers(headers)
    configured_token = str(token or os.getenv("OPAI_MOMO_PROTOCOL_TOKEN") or os.getenv("OPAI_MOMO_API_TOKEN") or "").strip()
    configured_access_key = str(access_key or os.getenv("OPAI_MOMO_PROTOCOL_ACCESS_KEY") or "").strip()
    configured_secret_key = str(secret_key or os.getenv("OPAI_MOMO_PROTOCOL_SECRET_KEY") or "").strip()
    configured_mode = str(auth_mode if auth_mode is not None else (os.getenv("OPAI_MOMO_PROTOCOL_AUTH_MODE") or ("bearer" if configured_token else "none"))).strip().lower()
    if configured_mode == "bearer" and not configured_token:
        raise ValueError("Bearer 模式缺少协议 Token")
    if configured_mode == "hmac_sha256" and (not configured_access_key or not configured_secret_key):
        raise ValueError("HMAC-SHA256 模式缺少协议 Access Key 或 Secret Key")
    return DirectMomoProvider(
        base_url,
        timeout=timeout,
        headers=configured,
        routes=_parse_routes(routes or os.getenv("OPAI_MOMO_PROTOCOL_ROUTES")),
        auth_mode=configured_mode,
        token=configured_token,
        access_key=configured_access_key,
        secret_key=configured_secret_key,
        signature_header=signature_header or os.getenv("OPAI_MOMO_PROTOCOL_SIGNATURE_HEADER", "X-Signature"),
    )
