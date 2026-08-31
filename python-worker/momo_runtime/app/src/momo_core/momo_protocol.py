"""MoMo provider boundary.

The runtime never imports the GoPay protocol.  ``LocalMomoProvider`` is used
for deterministic local workflow checks; ``HttpMomoProvider`` is an explicit
adapter boundary for a supplied MoMo-compatible test or production service.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from .momo_models import MomoProfile, MomoQrOrder, ProviderResult


class MobileWalletProvider(ABC):
    @abstractmethod
    def start_register(self, phone: str, proxy: str = "") -> ProviderResult: ...

    @abstractmethod
    def send_otp(self, session: dict[str, Any]) -> ProviderResult: ...

    @abstractmethod
    def verify_otp(self, session: dict[str, Any], code: str) -> ProviderResult: ...

    @abstractmethod
    def submit_profile(self, session: dict[str, Any], profile: MomoProfile) -> ProviderResult: ...

    @abstractmethod
    def set_pin(self, session: dict[str, Any], pin: str) -> ProviderResult: ...

    @abstractmethod
    def login(self, phone: str, pin: str = "", proxy: str = "") -> ProviderResult: ...

    @abstractmethod
    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult: ...

    def execute(self, operation: str, payload: dict[str, Any]) -> ProviderResult:
        """Dispatch the small operation set used by the task state machine."""
        if operation == "login":
            return self.login(str(payload.get("phone") or ""), str(payload.get("pin") or ""), str(payload.get("proxy") or ""))
        if operation == "payment":
            return self.scan_pay(payload, MomoQrOrder(str(payload.get("qr_payload") or ""), str(payload.get("amount") or "")))
        if operation == "register":
            session = {"phone": str(payload.get("phone") or ""), "otp": str(payload.get("otp") or "")}
            checked = self.verify_otp(session, session["otp"])
            if not checked.ok:
                return checked
            profile = MomoProfile(session["phone"], skip_kyc=bool(payload.get("skip_kyc", True)))
            submitted = self.submit_profile(session, profile)
            if not submitted.ok:
                return submitted
            return self.set_pin(session, str(payload.get("pin") or ""))
        return ProviderResult(False, {}, f"unsupported MoMo operation: {operation}")


class LocalMomoProvider(MobileWalletProvider):
    """Deterministic provider for local UI and state-machine verification."""

    def start_register(self, phone: str, proxy: str = "") -> ProviderResult:
        return ProviderResult(True, {"phone": phone, "proxy": proxy})

    def send_otp(self, session: dict[str, Any]) -> ProviderResult:
        return ProviderResult(True, {"sent": True})

    def verify_otp(self, session: dict[str, Any], code: str) -> ProviderResult:
        return ProviderResult(bool(code), {"otp_verified": bool(code)}, "OTP is empty" if not code else "")

    def submit_profile(self, session: dict[str, Any], profile: MomoProfile) -> ProviderResult:
        return ProviderResult(True, {"kyc_status": "skipped" if profile.skip_kyc else "pending"})

    def set_pin(self, session: dict[str, Any], pin: str) -> ProviderResult:
        return ProviderResult(True, {"pin_set": bool(pin)})

    def login(self, phone: str, pin: str = "", proxy: str = "") -> ProviderResult:
        return ProviderResult(True, {"phone": phone, "session_ready": True})

    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult:
        return ProviderResult(bool(order.payload), {"order_accepted": bool(order.payload)}, "QR payload is empty" if not order.payload else "")


class HttpMomoProvider(MobileWalletProvider):
    """Thin JSON adapter. Endpoint semantics are supplied by the MoMo adapter service."""

    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, operation: str, payload: dict[str, Any]) -> ProviderResult:
        request = urllib.request.Request(f"{self.base_url}/{operation}", data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
            if not isinstance(body, dict):
                body = {"value": body}
            return ProviderResult(bool(body.get("ok", True)), body, str(body.get("error") or ""))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            return ProviderResult(False, {}, str(exc))

    def start_register(self, phone: str, proxy: str = "") -> ProviderResult: return self._call("register/start", {"phone": phone, "proxy": proxy})
    def send_otp(self, session: dict[str, Any]) -> ProviderResult: return self._call("register/send-otp", session)
    def verify_otp(self, session: dict[str, Any], code: str) -> ProviderResult: return self._call("register/verify-otp", {**session, "code": code})
    def submit_profile(self, session: dict[str, Any], profile: MomoProfile) -> ProviderResult: return self._call("register/profile", {**session, "profile": profile.__dict__})
    def set_pin(self, session: dict[str, Any], pin: str) -> ProviderResult: return self._call("register/pin", {**session, "pin": pin})
    def login(self, phone: str, pin: str = "", proxy: str = "") -> ProviderResult: return self._call("login", {"phone": phone, "pin": pin, "proxy": proxy})
    def scan_pay(self, session: dict[str, Any], order: MomoQrOrder) -> ProviderResult: return self._call("payment/scan", {**session, "order": order.__dict__})


def build_provider(*, mock_mode: bool, base_url: str) -> MobileWalletProvider:
    if mock_mode:
        return LocalMomoProvider()
    if not base_url:
        raise ValueError("MoMo API Base URL is required when local mode is disabled")
    return HttpMomoProvider(base_url)
