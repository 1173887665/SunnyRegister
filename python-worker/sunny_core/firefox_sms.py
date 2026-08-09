from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests


FIREFOX_DEFAULT_BASE_URL = "https://www.firefox.fun/yhapi.ashx"
FIREFOX_DEFAULT_SERVICE = "1096"
FIREFOX_POLL_INTERVAL_SECONDS = 5
FIREFOX_RELEASE_DELAY_SECONDS = 35


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _as_float(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normal_phone(country_code: str, phone: str) -> str:
    country_code = re.sub(r"\D", "", country_code)
    phone = re.sub(r"\D", "", phone)
    if not phone:
        return ""
    if country_code and not phone.startswith(country_code):
        phone = country_code + phone
    return "+" + phone


def _api_url(value: Any) -> str:
    raw = _clean(value, FIREFOX_DEFAULT_BASE_URL)
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("FireFox API URL must be a valid HTTP or HTTPS URL")
    scheme = "https" if (parsed.hostname or "").lower() in {"www.firefox.fun", "web.firefox.fun"} else parsed.scheme
    path = parsed.path.rstrip("/")
    if not path:
        path = "/yhapi.ashx"
    elif not path.lower().endswith("/yhapi.ashx"):
        path += "/yhapi.ashx"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


class FireFoxAPIError(RuntimeError):
    def __init__(self, action: str, code: str, message: str = ""):
        self.action = action
        self.code = code
        super().__init__(message or f"FireFox {action} failed: {code}")


@dataclass(slots=True)
class FireFoxActivation:
    pkey: str
    number: str
    country: str = ""
    country_code: str = ""
    location: str = ""
    port: str = ""


class FireFoxSMSClient:
    """FireFox SMS client following the provider's GET API protocol."""

    _ERRORS = {
        "login": {
            "-1": "API account is empty",
            "-2": "API account must contain 3-30 characters",
            "-3": "API account cannot contain '|'",
            "-4": "API account cannot contain Chinese characters",
            "-5": "API password is empty",
            "-6": "API password must contain 3-30 characters",
            "-7": "the previous login from this IP failed; verify the credentials and retry after one minute",
            "-8": "the account is disabled",
            "-9": "the API account or password is incorrect",
        },
        "myInfo": {
            "-1": "token is missing",
            "-2": "token is invalid",
            "-3": "account information can only be requested once every 60 seconds",
        },
        "getPhone": {
            "-1": "no number is currently available",
            "-2": "token is missing",
            "-3": "service ID does not exist",
            "-4": "country code is invalid",
            "-5": "service is not approved",
            "-6": "service is disabled",
            "-7": "account is disabled",
            "-8": "account balance is insufficient",
            "-9": "too many occupied numbers; release unused numbers before retrying",
            "-10": "the service does not allow requesting a specified number",
        },
        "getPhoneCode": {
            "-1": "token is missing",
            "-2": "pkey is invalid",
            "-3": "waiting for the verification code",
            "-4": "the number is offline or already released",
            "-5": "the number was forcibly blacklisted",
        },
        "setRel": {
            "-1": "token is missing",
            "-2": "pkey is invalid",
            "-3": "the number does not exist or was already released",
            "-4": "a code was received, so the number cannot be released",
            "-5": "an outgoing SMS was submitted, so the number cannot be released",
            "-6": "the release limit was exceeded and the number was blacklisted",
        },
    }

    _TOKEN_ERRORS = {
        "myInfo": {"-1", "-2"},
        "getPhone": {"-2"},
        "getPhoneCode": {"-1"},
        "setRel": {"-1"},
    }

    def __init__(self, config: dict[str, Any], proxies: dict[str, str] | None = None):
        self.base_url = _api_url(config.get("firefox_base_url"))
        self.api_name = _clean(config.get("firefox_api_name"))
        self.password = _clean(config.get("firefox_password"))
        self.country = _clean(config.get("firefox_default_country"))
        self.service = _clean(config.get("firefox_default_service"), FIREFOX_DEFAULT_SERVICE)
        self.max_price = _as_float(config.get("firefox_max_price"), 0)
        self.proxies = proxies or None
        self._token = ""
        if not self.api_name or not self.password.strip():
            raise RuntimeError("FireFox API account or password is not configured")
        if not 3 <= len(self.api_name) <= 30:
            raise RuntimeError("FireFox API account must contain 3-30 characters")
        if "|" in self.api_name or re.search(r"[\u3400-\u9fff]", self.api_name):
            raise RuntimeError("FireFox API account cannot contain '|' or Chinese characters")
        if not 3 <= len(self.password) <= 30:
            raise RuntimeError("FireFox API password must contain 3-30 characters (official login error -6)")
        if not self.country:
            raise RuntimeError("FireFox country is not configured")
        if not self.service:
            raise RuntimeError("FireFox service is not configured")
        if self.max_price <= 0:
            raise RuntimeError("FireFox max price must be greater than 0")

    def _request_raw(self, action: str, timeout: int = 30, **params: Any) -> tuple[bool, list[str], str]:
        payload = {"act": action, **{key: value for key, value in params.items() if value is not None and value != ""}}
        response = requests.get(self.base_url, params=payload, timeout=timeout, proxies=self.proxies)
        response.raise_for_status()
        raw = response.text.strip().lstrip("\ufeff")
        if not raw:
            raise RuntimeError(f"FireFox {action} returned an empty response")
        parts = [part.strip() for part in raw.split("|")]
        return parts[0] == "1", parts, raw

    def _request(self, action: str, timeout: int = 30, **params: Any) -> list[str]:
        ok, parts, raw = self._request_raw(action, timeout=timeout, **params)
        if ok:
            return parts
        code = parts[1] if len(parts) > 1 else raw
        message = self._error_message(action, code, raw)
        raise FireFoxAPIError(action, code, f"FireFox {action} failed ({code}): {message}")

    def _error_message(self, action: str, code: str, raw: str) -> str:
        return self._ERRORS.get(action, {}).get(code, raw)

    def _authorized_raw(self, action: str, timeout: int = 30, **params: Any) -> tuple[bool, list[str], str]:
        for attempt in range(2):
            ok, parts, raw = self._request_raw(action, timeout=timeout, token=self.login(), **params)
            code = parts[1] if len(parts) > 1 else raw
            if not ok and code in self._TOKEN_ERRORS.get(action, set()) and attempt == 0:
                self._token = ""
                continue
            return ok, parts, raw
        raise RuntimeError(f"FireFox {action} authorization retry was exhausted")

    def _authorized(self, action: str, timeout: int = 30, **params: Any) -> list[str]:
        ok, parts, raw = self._authorized_raw(action, timeout=timeout, **params)
        if ok:
            return parts
        code = parts[1] if len(parts) > 1 else raw
        message = self._error_message(action, code, raw)
        raise FireFoxAPIError(action, code, f"FireFox {action} failed ({code}): {message}")

    def login(self) -> str:
        if not self._token:
            parts = self._request("login", ApiName=self.api_name, PassWord=self.password)
            if len(parts) < 2 or not parts[1]:
                raise RuntimeError("FireFox login did not return a token")
            self._token = parts[1]
        return self._token

    def balance(self) -> str:
        parts = self._authorized("myInfo")
        if len(parts) < 2:
            raise RuntimeError("FireFox account info did not return a balance")
        return parts[1]

    def get_number(self) -> FireFoxActivation:
        parts = self._authorized(
            "getPhone",
            timeout=45,
            iid=self.service,
            country=self.country,
            maxPrice=f"{self.max_price:g}",
            otpmode="sms",
        )
        if len(parts) < 9:
            raise RuntimeError("FireFox getPhone returned an incomplete response: " + "|".join(parts))
        number = _normal_phone(parts[4], parts[7])
        if not parts[1] or not number:
            raise RuntimeError("FireFox getPhone did not return a pkey and phone number")
        return FireFoxActivation(
            pkey=parts[1],
            number=number,
            country=parts[3],
            country_code=parts[4],
            location=parts[5],
            port=parts[6],
        )

    def get_code(self, pkey: str) -> str | None:
        ok, parts, raw = self._authorized_raw("getPhoneCode", timeout=20, pkey=pkey)
        if ok:
            code = parts[1] if len(parts) > 1 else ""
            if not re.fullmatch(r"\d{6}", code) and len(parts) > 2:
                match = re.search(r"(?<!\d)(\d{6})(?!\d)", parts[2])
                code = match.group(1) if match else ""
            if not re.fullmatch(r"\d{6}", code):
                raise RuntimeError("FireFox getPhoneCode did not return an independent 6-digit code")
            return code
        error_code = parts[1] if len(parts) > 1 else raw
        if error_code == "-3":
            return None
        raise FireFoxAPIError("getPhoneCode", error_code, f"FireFox getPhoneCode failed ({error_code}): {self._error_message('getPhoneCode', error_code, raw)}")

    def wait_code(self, pkey: str, timeout: int = 180, log: Callable[[str], None] | None = None) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self.get_code(pkey)
            if code:
                if log:
                    log(f"FireFox received a {len(code)}-digit code (redacted)")
                return code
            if log:
                log(f"FireFox waiting for code, local timeout {max(0, int(deadline - time.monotonic()))}s")
            time.sleep(min(FIREFOX_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
        raise TimeoutError("FireFox code timeout")

    def release(self, pkey: str, max_attempts: int = 4) -> None:
        if not pkey:
            return
        for attempt in range(max_attempts):
            ok, parts, raw = self._authorized_raw("setRel", timeout=20, pkey=pkey)
            if ok:
                return
            code = parts[1] if len(parts) > 1 else raw
            if code.isdigit() and int(code) > 0 and attempt + 1 < max_attempts:
                time.sleep(int(code))
                continue
            if code in {"-3", "-4"}:
                return
            raise FireFoxAPIError("setRel", code, f"FireFox setRel failed ({code}): {self._error_message('setRel', code, raw)}")

    def release_later(self, pkey: str, delay: int = FIREFOX_RELEASE_DELAY_SECONDS) -> threading.Thread:
        def run() -> None:
            time.sleep(max(0, delay))
            try:
                self.release(pkey)
            except Exception:
                # Release is best-effort and must not interrupt the active registration flow.
                return

        thread = threading.Thread(target=run, name=f"firefox-release-{pkey[:8]}", daemon=False)
        thread.start()
        return thread
