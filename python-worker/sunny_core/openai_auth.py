from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, unquote, urlparse

import requests

from .auth_challenges import generate_totp
from .browser_backend import open_registration_browser
from .mailbox import MailAccount, create_mailbox_reader
from .proxy import proxy_dict
from .sentinel import browser_fetch, build_sentinel_token, generate_datadog_trace_headers

AUTH_BASE_URL = "https://auth.openai.com"
CHATGPT_BASE_URL = "https://chatgpt.com"
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
AUTH_OAUTH_TOKEN_URLS = [f"{AUTH_BASE_URL}/api/oauth/oauth2/token", f"{AUTH_BASE_URL}/oauth/token"]
FIRST_NAMES = ["Ethan", "Noah", "Liam", "Mason", "Lucas", "Logan", "Owen", "Ryan", "Leo", "Adam", "Ella", "Ava", "Mia", "Luna", "Chloe", "Grace", "Ruby", "Nora", "Ivy", "Sofia"]
LAST_NAMES = ["Smith", "Brown", "Taylor", "Walker", "Wilson", "Clark", "Hall", "Young", "Allen", "King", "Scott", "Green", "Baker", "Adams", "Turner"]
REGISTER_DEVICE_PROFILES = [
    {"locale": "ja-JP", "languages": ["ja-JP", "ja"], "timezone": "Asia/Tokyo"},
]

# Phone binding must follow the number itself. Provider country identifiers are
# not consistent (for example FireFox uses "mys"), while the E.164 prefix is.
PHONE_COUNTRIES_BY_DIAL = {
    "1": {"iso": "US", "name": "United States", "aliases": ("us", "usa", "united states", "america")},
    "60": {"iso": "MY", "name": "Malaysia", "aliases": ("my", "mys", "malaysia")},
    "86": {"iso": "CN", "name": "China", "aliases": ("cn", "chn", "china")},
}

class TaskCancelledError(RuntimeError):
    pass


class BrowserDriverDisconnectedError(RuntimeError):
    pass


class PhoneBindingUnavailableError(RuntimeError):
    pass


_DRIVER_DISCONNECTED_MARKERS = (
    "connection closed while reading from the driver",
    "target page, context or browser has been closed",
    "browser has been closed",
    "playwright connection closed",
    "connection closed",
)

_NAVIGATION_ABORT_MARKERS = (
    "ns_binding_aborted",
    "net::err_aborted",
)


def _is_browser_driver_disconnected(error: Any) -> bool:
    message = str(error or "").strip().lower()
    return any(marker in message for marker in _DRIVER_DISCONNECTED_MARKERS)


def _is_navigation_aborted(error: Any) -> bool:
    message = str(error or "").strip().lower()
    return any(marker in message for marker in _NAVIGATION_ABORT_MARKERS)


def _auth_navigation_landed(page: Any, previous_url: str = "") -> bool:
    try:
        current_url = str(page.url or "")
        current = urlparse(current_url)
    except Exception:
        return False
    if not current_url or current_url == str(previous_url or ""):
        return False
    allowed_host = current.hostname in {"auth.openai.com", "chatgpt.com"}
    oauth_callback = current.hostname in {"localhost", "127.0.0.1"} and current.port == 1455
    return current.scheme in {"http", "https"} and (allowed_host or oauth_callback)


def _goto_auth_page(page: Any, url: str, log: Callable[[str], None] | None = None, *, timeout: int = 90000):
    """Navigate to auth while tolerating browser-engine redirect cancellation.

    Firefox/Camoufox reports NS_BINDING_ABORTED when an auth response replaces
    the requested document with an immediate redirect. It is only considered
    successful after the page has actually landed on an OpenAI/ChatGPT origin.
    """
    previous_url = str(getattr(page, "url", "") or "")
    try:
        return page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as exc:
        if not _is_navigation_aborted(exc):
            raise
        try:
            page.wait_for_timeout(600)
        except Exception:
            pass
        if _auth_navigation_landed(page, previous_url):
            if log:
                log(f"[认证] 认证导航由上游重定向接管，继续处理当前页面：{page.url}")
            return None
        try:
            return page.goto(url, wait_until="commit", timeout=min(timeout, 30000))
        except Exception as retry_exc:
            if _is_navigation_aborted(retry_exc) and _auth_navigation_landed(page, previous_url):
                if log:
                    log(f"[认证] 认证导航重试已进入目标站点，继续处理当前页面：{page.url}")
                return None
            raise


@dataclass
class DeviceFingerprint:
    user_agent: str
    locale: str
    languages: list[str]
    timezone: str
    viewport_width: int
    viewport_height: int
    screen_width: int
    screen_height: int
    outer_width: int
    outer_height: int
    device_scale_factor: float
    hardware_concurrency: int
    device_memory: int
    platform: str = "Win32"

    @property
    def accept_language(self) -> str:
        return ",".join([self.languages[0], *[f"{x};q={max(0.1, 1 - i * 0.1):.1f}" for i, x in enumerate(self.languages[1:], start=1)]])


def generate_register_fingerprint() -> DeviceFingerprint:
    profile = random.choice(REGISTER_DEVICE_PROFILES)
    viewport = random.choice([(1280, 720, 1280, 720, 1), (1365, 768, 1366, 768, 1), (1440, 900, 1440, 900, 1), (1536, 864, 1536, 864, 1.25), (1600, 900, 1600, 900, 1)])
    major = random.randint(134, 146)
    build = random.randint(6000, 9999)
    patch = random.randint(50, 220)
    return DeviceFingerprint(
        user_agent=f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.{build}.{patch} Safari/537.36",
        locale=profile["locale"],
        languages=list(profile["languages"]),
        timezone=profile["timezone"],
        viewport_width=viewport[0],
        viewport_height=viewport[1],
        screen_width=viewport[2],
        screen_height=viewport[3],
        outer_width=viewport[0] + random.randint(8, 16),
        outer_height=viewport[1] + random.randint(72, 96),
        device_scale_factor=viewport[4],
        hardware_concurrency=random.choice([4, 6, 8, 8, 12, 16]),
        device_memory=random.choice([4, 8, 8, 16]),
    )


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if log:
        log(message)


def openai_browser_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "user-agent": DEFAULT_USER_AGENT,
        "accept-language": "ja-JP,ja;q=0.9",
        "sec-ch-ua": '\"Google Chrome\";v=\"146\", \"Chromium\";v=\"146\", \"Not.A/Brand\";v=\"24\"',
        "sec-ch-ua-full-version-list": '\"Google Chrome\";v=\"146.0.0.0\", \"Chromium\";v=\"146.0.0.0\", \"Not.A/Brand\";v=\"24.0.0.0\"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '\"Windows\"',
        "sec-ch-ua-platform-version": '\"15.0.0\"',
    }
    if extra:
        headers.update(extra)
    return headers


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = str(token).split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()).decode("utf-8"))
    except Exception:
        return {}


def random_urlsafe_string(length: int) -> str:
    return secrets.token_urlsafe(max(1, length))[:length]


def pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _nested(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def normalize_auth_record(email: str, payload: dict[str, Any]) -> dict[str, Any]:
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise RuntimeError(f"OpenAI token response missing access_token: {payload}")
    claims = decode_jwt_payload(access_token)
    id_claims = decode_jwt_payload(str(payload.get("id_token") or ""))
    auth = _nested(claims, "https://api.openai.com/auth")
    id_auth = _nested(id_claims, "https://api.openai.com/auth")
    organizations = auth.get("organizations")
    if not isinstance(organizations, list):
        organizations = id_auth.get("organizations")
    if not isinstance(organizations, list):
        organizations = []
    default_organization = next(
        (item for item in organizations if isinstance(item, dict) and item.get("is_default")),
        next((item for item in organizations if isinstance(item, dict)), {}),
    )
    exp = int(claims.get("exp") or 0)
    return {
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token") or ""),
        "id_token": str(payload.get("id_token") or ""),
        "client_id": DEFAULT_CLIENT_ID,
        "account_id": _first_text(auth.get("chatgpt_account_id"), auth.get("account_id"), id_auth.get("chatgpt_account_id")),
        "chatgpt_user_id": _first_text(auth.get("chatgpt_user_id"), auth.get("user_id"), id_auth.get("chatgpt_user_id"), id_claims.get("sub"), claims.get("sub")),
        "organization_id": _first_text(auth.get("poid"), id_auth.get("poid"), default_organization.get("id")),
        "email": _first_text(id_claims.get("email"), claims.get("email"), email),
        "expired": datetime.fromtimestamp(exp, timezone.utc).isoformat().replace("+00:00", "Z") if exp else "",
        "expires_at": exp,
        "plan_type": _first_text(auth.get("chatgpt_plan_type"), id_auth.get("chatgpt_plan_type")),
        "raw_token_payload": payload,
    }


def refresh_openai_access_token(openai_rt: str, proxy_url: str = "") -> dict[str, Any]:
    if not str(openai_rt or "").startswith("rt_"):
        raise RuntimeError("Invalid OpenAI refresh_token")
    session = requests.Session()
    session.proxies.update(proxy_dict(proxy_url))
    last_error = ""
    for endpoint in AUTH_OAUTH_TOKEN_URLS:
        response = session.post(
            endpoint,
            headers=openai_browser_headers({"accept": "application/json", "content-type": "application/x-www-form-urlencoded"}),
            data={"grant_type": "refresh_token", "client_id": DEFAULT_CLIENT_ID, "refresh_token": openai_rt},
            timeout=45,
        )
        if response.ok:
            payload = response.json()
            if payload.get("access_token"):
                return payload
        last_error = f"endpoint={endpoint} HTTP {response.status_code} {response.text[:300]}"
    raise RuntimeError(f"OpenAI RT refresh access_token failed: {last_error}")


def fetch_session(access_token: str, proxy_url: str = "") -> dict[str, Any]:
    session = requests.Session()
    session.proxies.update(proxy_dict(proxy_url))
    response = session.get(
        f"{CHATGPT_BASE_URL}/api/auth/session",
        headers=openai_browser_headers({"accept": "application/json", "authorization": f"Bearer {access_token}", "referer": f"{CHATGPT_BASE_URL}/"}),
        timeout=45,
    )
    if not response.ok:
        return {"access_token": access_token, "session_json": {"accessToken": access_token, "fetch_session_error": f"HTTP {response.status_code} {response.text[:200]}"}}
    data = response.json()
    return {"access_token": data.get("accessToken") or data.get("access_token") or access_token, "session_json": data}


def random_profile() -> tuple[str, str]:
    age = random.randint(25, 34)
    today = datetime.now(timezone.utc)
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}", f"{today.year - age:04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"


def _phone_country_context(phone: dict[str, Any], number: str) -> dict[str, Any]:
    raw_number = str(number or "").strip()
    number_digits = re.sub(r"\D", "", raw_number)
    provider_dial = re.sub(r"\D", "", str(phone.get("country_code") or phone.get("dial_code") or ""))
    number_dial = ""
    if raw_number.startswith("+"):
        number_dial = next(
            (dial for dial in sorted(PHONE_COUNTRIES_BY_DIAL, key=len, reverse=True) if number_digits.startswith(dial)),
            "",
        )
    dial_code = number_dial or provider_dial
    country = PHONE_COUNTRIES_BY_DIAL.get(dial_code, {})
    country_iso = str(country.get("iso") or "").upper()
    raw_hints = [phone.get("country_iso"), phone.get("country"), phone.get("country_name")]
    hints: list[str] = []
    for value in [country_iso, country.get("name"), *(country.get("aliases") or ())]:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
        if normalized and normalized not in hints:
            hints.append(normalized)
    for value in raw_hints:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
        if normalized and not normalized.isdigit() and normalized not in hints:
            hints.append(normalized)
    explicit_non_us = bool(country_iso and country_iso != "US")
    should_select = explicit_non_us or (dial_code != "1" if dial_code else bool(number_digits and not number_digits.startswith("1")))
    return {
        "dial_code": dial_code,
        "country_iso": country_iso,
        "hints": hints,
        "number_digits": number_digits,
        "should_select": should_select,
    }


def _country_option_score(text: str, values: list[str], context: dict[str, Any]) -> tuple[int, str]:
    combined = " ".join([str(text or ""), *[str(value or "") for value in values]])
    normalized = re.sub(r"[^a-z0-9+]+", " ", combined.casefold()).strip()
    score = 0
    wanted_iso = str(context.get("country_iso") or "").casefold()
    normalized_values = [re.sub(r"[^a-z0-9]+", "", str(value or "").casefold()) for value in values]
    if wanted_iso and wanted_iso in normalized_values:
        score = 200
    for hint in context.get("hints") or []:
        if normalized == hint:
            score = max(score, 140)
        elif re.search(rf"(?<![a-z0-9]){re.escape(hint)}(?![a-z0-9])", normalized):
            score = max(score, 100 + min(len(hint), 20))

    option_dials: list[str] = []
    for match in re.findall(r"\+(\d[\d\s().-]{0,8})", combined):
        digits = re.sub(r"\D", "", match)
        if digits and digits not in option_dials:
            option_dials.append(digits)
    for value in values:
        raw = str(value or "").strip()
        if raw.startswith("+"):
            digits = re.sub(r"\D", "", raw)
            if digits and digits not in option_dials:
                option_dials.append(digits)

    wanted_dial = str(context.get("dial_code") or "")
    matched_dial = ""
    if wanted_dial and wanted_dial in option_dials:
        score = max(score, 180)
        matched_dial = wanted_dial
    elif not wanted_dial:
        number_digits = str(context.get("number_digits") or "")
        matches = [dial for dial in option_dials if number_digits.startswith(dial)]
        if matches:
            matched_dial = max(matches, key=len)
            score = max(score, 80 + len(matched_dial))
    return score, matched_dial


def _phone_number_candidates(number: str, dial_code: str = "") -> list[str]:
    raw = str(number or "").strip()
    digits = re.sub(r"\D", "", raw)
    dial = re.sub(r"\D", "", str(dial_code or ""))
    national = digits[len(dial):] if dial and digits.startswith(dial) else ""
    candidates: list[str] = []
    for value in [national, raw, digits]:
        if value and value not in candidates:
            candidates.append(value)
    return candidates


class OpenAIEmailRegisterFlow:
    """SunnyRegister in-project email register/login flow, following the original register-or-login implementation."""

    def __init__(self, account: MailAccount, proxy_url: str, headless: bool, log: Callable[[str], None] | None, phone_provider=None, existing_account: bool = False, require_refresh_token: bool = True, should_cancel: Callable[[], bool] | None = None, execution_mode: str = "", on_progress: Callable[[str, dict[str, Any]], None] | None = None, mailbox_proxy_url: str | None = None):
        self.account = account
        self.proxy_url = proxy_url
        self.mailbox_proxy_url = proxy_url if mailbox_proxy_url is None else mailbox_proxy_url
        self.headless = headless
        self.execution_mode = (execution_mode or ("background" if headless else "visible")).strip().lower()
        self.log = log or (lambda _m: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.on_progress = on_progress
        self.phone_provider = phone_provider
        self.otp_reader: Any | None = None
        self.existing_account = existing_account
        self.fingerprint = generate_register_fingerprint()
        self.auth_action = "login" if existing_account else "unknown"
        self.require_refresh_token = require_refresh_token
        self.phone_verification_completed = False
        self.browser_backend = "camoufox" if headless else "chromium"
        self.device_id = ""
        self.generated_password = ""

    def _check_cancelled(self) -> None:
        if self.should_cancel():
            self.log("[系统] 用户已请求中断任务，正在停止当前注册/登录流程")
            raise TaskCancelledError("Task cancelled by user")

    def _sleep_checked(self, seconds: float) -> None:
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            self._check_cancelled()
            time.sleep(min(0.5, max(0.0, deadline - time.time())))
        self._check_cancelled()

    def _emit_progress(self, stage: str, data: dict[str, Any] | None = None) -> None:
        if not self.on_progress:
            return
        try:
            self.on_progress(stage, dict(data or {}))
        except Exception as exc:
            self.log(f"[系统] 保存任务阶段检查点失败：{stage}: {exc}")

    def run(self) -> dict[str, Any]:
        self.log(f"[认证] 开始注册或登录: {self.account.email}")
        try:
            self._check_cancelled()
            if not (self.account.mailbox_type == "apple" and self.account.mailbox_channel == "url_api" and not self.account.access_key):
                self._preconnect_otp_reader()
            self._check_cancelled()
            mode_label = "后台浏览器自动（Camoufox Headless，无窗口）" if self.headless else "可视浏览器自动（Chromium Visible，有窗口）"
            self.log(f"[认证] 执行方式：{mode_label}")
            with open_registration_browser(
                headless=self.headless,
                proxy_url=self.proxy_url,
                fingerprint=self.fingerprint,
                log=self.log,
            ) as browser_session:
                context = browser_session.context
                self.browser_backend = browser_session.backend
                if self.browser_backend == "chromium":
                    self._install_stealth(context)
                else:
                    context.set_extra_http_headers({"Accept-Language": self.fingerprint.accept_language})
                context.clear_cookies()
                self.log(
                    f"[认证] 已启动隔离无痕浏览器上下文，后端 {self.browser_backend}，"
                    f"语言环境 {self.fingerprint.locale} / {self.fingerprint.timezone}"
                )
                self._check_cancelled()
                page = context.new_page()
                self._log_runtime_fingerprint(page)
                landing_response = page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
                if landing_response and landing_response.status >= 400:
                    self.log(f"[认证] ChatGPT 首页返回 HTTP {landing_response.status}，继续尝试通过浏览器会话初始化认证")
                self._check_cancelled()
                signin_url = self._create_openai_signin_url(context, page)
                otp_min_timestamp = time.time() - 10
                _goto_auth_page(page, signin_url, self.log, timeout=90000)
                self._emit_progress("browser_started")
                if self.headless:
                    self.log("[认证] 已打开 OpenAI 认证页，后台状态机开始自动处理注册/登录")
                else:
                    self.log("[认证] 已打开 OpenAI 认证页；如出现交互式验证，可在当前浏览器窗口处理")
                try:
                    self._drive_register_or_login(page, otp_min_timestamp)
                except Exception as exc:
                    error_text = str(exc)
                    if "phone verification" not in error_text.lower():
                        raise
                    original_require_refresh_token = self.require_refresh_token
                    self.require_refresh_token = False
                    try:
                        result = self._extract_session_info(context, page)
                    except Exception:
                        raise exc
                    finally:
                        self.require_refresh_token = original_require_refresh_token
                    result["post_registration_error"] = error_text
                    result["auth_action"] = self.auth_action if self.auth_action != "unknown" else "login"
                    if self.generated_password:
                        result["generated_chatgpt_password"] = self.generated_password
                    self.log("[认证] ChatGPT 注册/登录已经完成，但手机号阶段无法继续；已保存 Session 并保留已注册状态")
                    return result
                result = self._extract_session_info(context, page)
                result["auth_action"] = self.auth_action if self.auth_action != "unknown" else "login"
                if self.generated_password:
                    result["generated_chatgpt_password"] = self.generated_password
                self.log("[认证] 注册或登录完成，已读取 Session 信息")
                return result
        finally:
            if self.otp_reader:
                self.otp_reader.close()

    def _log_runtime_fingerprint(self, page) -> None:
        try:
            snapshot = page.evaluate(
                """() => ({
                    userAgent: navigator.userAgent,
                    language: navigator.language,
                    languages: navigator.languages,
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    platform: navigator.platform,
                    webdriver: navigator.webdriver,
                    screen: `${screen.width}x${screen.height}`,
                })"""
            )
        except Exception as exc:
            self.log(f"[认证] 浏览器运行时指纹读取失败：{str(exc)[:200]}")
            return
        self.log(
            "[认证] 浏览器运行时指纹 "
            f"backend={self.browser_backend} locale={snapshot.get('language') or '-'} "
            f"timezone={snapshot.get('timezone') or '-'} platform={snapshot.get('platform') or '-'} "
            f"screen={snapshot.get('screen') or '-'} webdriver={snapshot.get('webdriver')}"
        )

    def _install_stealth(self, context) -> None:
        context.set_extra_http_headers(openai_browser_headers({"user-agent": self.fingerprint.user_agent, "Accept-Language": self.fingerprint.accept_language}))
        fp_json = json.dumps({"platform": self.fingerprint.platform, "languages": self.fingerprint.languages, "hardwareConcurrency": self.fingerprint.hardware_concurrency, "deviceMemory": self.fingerprint.device_memory}, ensure_ascii=False)
        context.add_init_script("""(() => {
            const fp = {fp_json};
            try { Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => undefined, configurable: true }); } catch (_) {}
            try { Object.defineProperty(Navigator.prototype, 'platform', { get: () => fp.platform, configurable: true }); } catch (_) {}
            try { Object.defineProperty(Navigator.prototype, 'languages', { get: () => fp.languages, configurable: true }); } catch (_) {}
            try { Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', { get: () => fp.hardwareConcurrency, configurable: true }); } catch (_) {}
            try { Object.defineProperty(Navigator.prototype, 'deviceMemory', { get: () => fp.deviceMemory, configurable: true }); } catch (_) {}
        })();""".replace("{fp_json}", fp_json))

    def _preconnect_otp_reader(self) -> None:
        if self.otp_reader:
            return
        provider = f"{self.account.mailbox_channel} iCloud API" if self.account.mailbox_type == "apple" else "Outlook Graph/IMAP"
        self.log(f"[邮箱] 提前连接 {provider}，准备接收 OpenAI 验证码")
        self.otp_reader = create_mailbox_reader(self.account, self.log, self.mailbox_proxy_url)
        self.otp_reader.connect()

    def _create_openai_signin_url(self, context, page=None) -> str:
        csrf_value, device_id = self._get_chatgpt_csrf_and_device(context, page)
        if not csrf_value:
            raise RuntimeError("ChatGPT CSRF 初始化失败：浏览器页面与后备接口均未返回 CSRF token")
        device_id = device_id or str(uuid.uuid4())
        self.device_id = device_id
        query = urlencode({
            "prompt": "login",
            "ext-oai-did": device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "ext-passkey-client-capabilities": "0111",
            "screen_hint": "login" if self.existing_account else "signup",
            "login_hint": self.account.email,
            "locale": self.fingerprint.locale,
        })
        signin_endpoint = f"{CHATGPT_BASE_URL}/api/auth/signin/openai?{query}"
        payload = None
        browser_error = ""
        if page is not None:
            try:
                browser_response = page.evaluate(
                    """async ({url, callbackUrl, csrfToken}) => {
                        const body = new URLSearchParams({callbackUrl, csrfToken, json: 'true'});
                        const response = await fetch(url, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded'},
                            body: body.toString(),
                        });
                        const text = await response.text();
                        return {ok: response.ok, status: response.status, text};
                    }""",
                    {"url": signin_endpoint, "callbackUrl": f"{CHATGPT_BASE_URL}/", "csrfToken": csrf_value},
                )
                if browser_response.get("ok"):
                    payload = json.loads(str(browser_response.get("text") or "{}"))
                else:
                    browser_error = f"HTTP {browser_response.get('status')} {str(browser_response.get('text') or '')[:300]}"
            except Exception as exc:
                browser_error = str(exc)
        if payload is None:
            if browser_error:
                self.log(f"[认证] 浏览器内 signin 请求未成功，切换后备请求：{browser_error[:300]}")
            response = context.request.post(
                signin_endpoint,
                form={"callbackUrl": f"{CHATGPT_BASE_URL}/", "csrfToken": csrf_value, "json": "true"},
                headers={"Accept": "application/json", "Accept-Language": self.fingerprint.accept_language},
                timeout=30000,
            )
            if not response.ok:
                raise RuntimeError(f"打开 OpenAI 认证页失败: HTTP {response.status} {response.text()[:300]}")
            payload = response.json()
        signin_url = str(payload.get("url") or "")
        if not signin_url:
            raise RuntimeError(f"Auth response missing redirect URL: {payload}")
        return signin_url

    def _get_chatgpt_csrf_and_device(self, context, page=None) -> tuple[str, str]:
        csrf_value = ""
        device_id = ""
        for cookie in context.cookies([CHATGPT_BASE_URL, "https://openai.com"]):
            if cookie.get("name") == "__Host-next-auth.csrf-token":
                csrf_value = unquote(cookie.get("value", "")).split("|")[0]
            if cookie.get("name") == "oai-did":
                device_id = cookie.get("value", "")
        if not csrf_value:
            browser_error = ""
            if page is not None:
                for attempt in range(1, 4):
                    self._check_cancelled()
                    try:
                        browser_response = page.evaluate(
                            """async () => {
                                const response = await fetch('/api/auth/csrf', {
                                    credentials: 'include',
                                    headers: {'Accept': 'application/json'},
                                });
                                const text = await response.text();
                                return {ok: response.ok, status: response.status, text};
                            }"""
                        )
                        if browser_response.get("ok"):
                            payload = json.loads(str(browser_response.get("text") or "{}"))
                            csrf_value = str(payload.get("csrfToken") or "").strip()
                            if csrf_value:
                                self.log("[认证] 已通过浏览器页面初始化 ChatGPT CSRF")
                                break
                        browser_error = f"HTTP {browser_response.get('status')} {str(browser_response.get('text') or '')[:180]}"
                    except Exception as exc:
                        browser_error = str(exc)
                    if attempt < 3:
                        self._sleep_checked(1)
                if not csrf_value and browser_error:
                    self.log(f"[认证] 浏览器页面 CSRF 初始化未成功，尝试后备接口：{browser_error[:240]}")
        if not csrf_value:
            try:
                response = context.request.get(
                    f"{CHATGPT_BASE_URL}/api/auth/csrf",
                    headers={"Accept": "application/json", "Accept-Language": self.fingerprint.accept_language, "Referer": f"{CHATGPT_BASE_URL}/"},
                    timeout=30000,
                )
                if response.ok:
                    csrf_value = str(response.json().get("csrfToken") or "").strip()
                else:
                    self.log(f"[认证] ChatGPT CSRF 后备接口返回 HTTP {response.status}: {response.text()[:240]}")
            except Exception as exc:
                self.log(f"[认证] ChatGPT CSRF 后备接口调用失败：{str(exc)[:240]}")
            if not csrf_value:
                for cookie in context.cookies([CHATGPT_BASE_URL, "https://openai.com"]):
                    if cookie.get("name") == "__Host-next-auth.csrf-token":
                        csrf_value = unquote(cookie.get("value", "")).split("|")[0]
                        break
        if not device_id:
            for cookie in context.cookies([CHATGPT_BASE_URL, "https://openai.com"]):
                if cookie.get("name") == "oai-did":
                    device_id = cookie.get("value", "")
                    break
        return csrf_value, device_id

    def _drive_register_or_login(self, page, otp_min_timestamp: float) -> None:
        deadline = time.time() + 600
        email_code_submitted = False
        about_you_submitted = False
        about_you_at = 0.0
        about_you_retry_at = 0.0
        about_you_recovery_attempted = False
        route_error_retries = 0
        last_progress_signature = ""
        last_progress_at = time.time()
        passive_reload_count = 0
        while time.time() < deadline:
            self._check_cancelled()
            if self._has_chatgpt_session(page):
                self._emit_progress("auth_completed")
                return
            signature = self._progress_signature(page)
            if signature and signature != last_progress_signature:
                last_progress_signature = signature
                last_progress_at = time.time()
                passive_reload_count = 0
            elif signature and time.time() - last_progress_at >= 90 and not self._page_needs_manual_attention(page):
                if passive_reload_count < 1:
                    passive_reload_count += 1
                    last_progress_at = time.time()
                    self.log(f"[认证] 页面长时间无进展，刷新当前页面后继续尝试：{self._page_text_summary(page, 160)}")
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=45000)
                    except Exception as exc:
                        self.log(f"[认证] 页面刷新失败，继续等待：{exc}")
                    continue
                raise RuntimeError(f"Register/login page stalled without progress: {self._page_text_summary(page, 300)}")
            error_text = self._detect_route_error(page)
            if error_text:
                if route_error_retries < 3 and self._retry_route_error(page):
                    route_error_retries += 1
                    self.log(f"[{self.account.email}] Page error; retried {route_error_retries}/3")
                    self._sleep_checked(4)
                    continue
                raise RuntimeError(f"OpenAI auth page error: {error_text}")
            url = str(page.url or "")
            if "add-phone" in url or "phone-verification" in url or self._has_phone_form(page):
                if self._handle_phone_if_possible(page):
                    email_code_submitted = False
                    about_you_submitted = False
                    continue
                raise RuntimeError("Phone verification required, but no usable phone pool is configured")
            if self._has_totp_challenge(page):
                self._submit_totp_challenge(page)
                continue
            if self._has_workspace_selection(page):
                self._select_first_workspace(page)
                continue
            if "password" in url and self._has_visible_password(page):
                if ("/log-in/password" in url or self.existing_account) and not self.account.chatgpt_password:
                    if not self.account.access_key or not self._switch_password_to_email_code(page):
                        raise RuntimeError("ChatGPT login requires a password or a configured email OTP endpoint")
                    email_code_submitted = False
                    about_you_submitted = False
                    continue
                self._fill_password_step(page)
                email_code_submitted = False
                about_you_submitted = False
                continue
            if "about-you" in url or self._has_about_you_form(page):
                email_code_submitted = False
                if about_you_submitted:
                    now = time.time()
                    if now - about_you_at >= 10 and now - about_you_retry_at >= 10 and self._about_you_current_values_ok(page):
                        self._click_continue(page)
                        about_you_retry_at = now
                        self.log("[认证] 基础资料已提交但页面未跳转，已重新点击提交按钮")
                    if not about_you_recovery_attempted and now - about_you_at >= 40 and self._about_you_current_values_ok(page):
                        about_you_recovery_attempted = True
                        if self._recover_after_profile_submit(page):
                            self._emit_progress("auth_completed")
                            return
                        email_code_submitted = False
                        about_you_submitted = False
                        about_you_at = 0.0
                        about_you_retry_at = 0.0
                        otp_min_timestamp = time.time() - 10
                        continue
                    self._sleep_checked(1)
                    continue
                self._fill_about_you(page)
                about_you_submitted = True
                about_you_at = time.time()
                about_you_retry_at = 0.0
                continue
            if "email-verification" in url or self._has_otp_input(page):
                if not email_code_submitted:
                    self._submit_email_code(page, otp_min_timestamp)
                    self._emit_progress("email_verified")
                    email_code_submitted = True
                self._sleep_checked(2)
                continue
            if self._fill_email_if_visible(page):
                otp_min_timestamp = time.time()
                email_code_submitted = False
                about_you_submitted = False
                continue
            about_you_submitted = False
            self._sleep_checked(2)
        raise TimeoutError(f"[{self.account.email}] Register/login flow timed out")

    def _progress_signature(self, page) -> str:
        try:
            url = str(page.url or "")
            text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=800)).strip()[:220]
            return f"{url}|{text}"
        except Exception:
            return str(getattr(page, "url", "") or "")

    def _page_needs_manual_attention(self, page) -> bool:
        try:
            text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=1000)).strip()
            value = f"{page.url} {text}".lower()
        except Exception:
            value = str(getattr(page, "url", "") or "").lower()
        return any(x in value for x in ["captcha", "cloudflare", "verify you are human", "challenge", "just a moment", "security check"])

    def _recover_after_profile_submit(self, page) -> bool:
        """Recover if profile submit likely succeeded but the auth page is stuck."""
        self.log("[认证] 基础资料提交后长时间未跳转，开始执行会话恢复检查")
        try:
            page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
            self._sleep_checked(2)
            if self._has_chatgpt_session(page):
                self.log("[认证] 会话恢复成功：ChatGPT Session 已可读取")
                return True
        except Exception as exc:
            self.log(f"[认证] ChatGPT 会话恢复检查失败：{exc}")
        old_existing = self.existing_account
        try:
            self.existing_account = True
            signin_url = self._create_openai_signin_url(page.context, page)
            _goto_auth_page(page, signin_url, self.log, timeout=90000)
            self.auth_action = "login"
            self.log("[认证] 已重新打开同一邮箱登录流程，用于恢复已创建账号的 Session")
        except Exception as exc:
            self.log(f"[认证] 重新打开登录流程失败，将回到主流程继续等待：{exc}")
        finally:
            self.existing_account = old_existing
        return False
    def _detect_route_error(self, page) -> str:
        try:
            text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=700)).strip()
        except Exception:
            return ""
        if any(x in text for x in ["Operation timed out", "Route Error", "Bad gateway", "Error code 502", "Route error"]):
            return text[:400]
        return ""

    def _retry_route_error(self, page) -> bool:
        for selector in ['button:has-text("Try again")', 'a:has-text("Try again")']:
            try:
                target = page.locator(selector).first
                if target.is_visible(timeout=800):
                    target.click(timeout=5000)
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    return True
            except Exception:
                pass
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            return True
        except Exception:
            return False

    def _visible_inputs(self, page, selectors: list[str]):
        out = []
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = min(loc.count(), 20)
            except Exception:
                continue
            for i in range(count):
                item = loc.nth(i)
                try:
                    if item.is_visible():
                        out.append(item)
                except Exception:
                    pass
        return out

    def _click_continue(self, page) -> bool:
        selectors = ['button:has-text("Finish creating account")', 'button:has-text("Create account")', 'button:has-text("Continue")', 'button:has-text("Next")', 'button:has-text("继续")', 'button:has-text("完成")', 'button[type="submit"]', '[role="button"]:has-text("Continue")', '[role="button"]:has-text("继续")']
        for selector in selectors:
            try:
                b = page.locator(selector).first
                if b.is_visible(timeout=700):
                    b.click(timeout=5000, force=True)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    return True
            except Exception:
                pass
        try:
            return bool(page.evaluate("""() => {
                const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
                const buttons = Array.from(document.querySelectorAll('button,[role="button"],input[type="submit"]')).filter(visible);
                const target = buttons.find(el => !el.disabled && el.getAttribute('aria-disabled') !== 'true' && /Continue|Next|Finish|Create/i.test(`${el.value||''} ${el.textContent||''} ${el.getAttribute('aria-label')||''}`)) || buttons.find(el => (el.type||'').toLowerCase()==='submit');
                if (!target) return false; target.scrollIntoView({block:'center'}); target.click(); return true;
            }"""))
        except Exception:
            return False

    def _fill_email_if_visible(self, page) -> bool:
        inputs = self._visible_inputs(page, ['input[type="email"]', 'input[name="email"]', 'input[name="username"]', 'input[autocomplete="email"]'])
        if not inputs:
            return False
        self.log("[认证] 填写邮箱并继续")
        inputs[0].fill(self.account.email)
        self._click_continue(page)
        self._emit_progress("email_submitted")
        return True

    def _has_otp_input(self, page) -> bool:
        if self._has_about_you_form(page) or self._looks_like_phone_code_page(page):
            return False
        return bool(self._visible_inputs(page, ['input[autocomplete="one-time-code"]', 'input[name="code"]', 'input[inputmode="numeric"]']))

    def _submit_email_code(self, page, min_timestamp: float) -> None:
        if not self.otp_reader:
            if self.account.mailbox_type == "apple" and self.account.mailbox_channel == "url_api" and not self.account.access_key:
                raise RuntimeError("Email OTP is required, but no url_api mail endpoint is configured")
            self.otp_reader = create_mailbox_reader(self.account, self.log, self.mailbox_proxy_url)
            self.otp_reader.connect()
        self.log("[邮箱] 等待 OpenAI 邮箱验证码")
        code = self.otp_reader.wait_for_code(min_timestamp, 180)
        journal, detach_journal = self._attach_email_otp_network_journal(page)
        try:
            # Existing accounts only need a fresh authenticated session. Filling
            # the OTP UI first can make the React form auto-submit before the
            # worker adds the device/Sentinel headers, producing a 403 and then
            # tripping the duplicate-code guard. Submit exactly once through the
            # authenticated browser fetch path used by the protocol flow.
            if self.browser_backend == "camoufox" and self.existing_account:
                continue_url = self._validate_email_code_api(page, code)
                self.log("[邮箱] 已通过续期登录浏览器会话提交邮箱验证码")
                if continue_url:
                    page.goto(continue_url, wait_until="domcontentloaded", timeout=90000)
                self._wait_after_otp_submit(page)
                return

            if not self._fill_email_code_inputs(page, code):
                raise RuntimeError("Email OTP input was not found")
            try:
                page.wait_for_timeout(250)
            except Exception:
                pass
            if self.browser_backend == "camoufox":
                if self._submit_email_code_form(page):
                    self.log("[邮箱] Camoufox 已通过页面原生控件提交邮箱验证码")
                    try:
                        self._wait_after_otp_submit(page)
                        return
                    except RuntimeError as exc:
                        detail = str(exc)
                        if self._is_invalid_email_otp_error(detail):
                            self.log("[邮箱] 页面拒绝了当前验证码；不会重复提交同一验证码，正在请求并等待新验证码")
                            self._retry_with_fresh_email_code(page, code)
                            return
                        if self._email_otp_validation_was_sent(journal):
                            raise RuntimeError(
                                "邮箱验证码已由页面提交，但注册状态未推进；为避免触发验证码尝试次数限制，"
                                f"已停止重复提交同一验证码。关键请求：{self._email_otp_network_summary(journal)}"
                            ) from exc
                        self.log(
                            "[邮箱] Camoufox 页面提交未推进注册状态，改用同一浏览器会话 "
                            f"Sentinel 接口校验：{detail[:220]}"
                        )
                else:
                    self.log("[邮箱] Camoufox 页面未找到可用提交控件，改用同一浏览器会话 Sentinel 接口校验")
                continue_url = self._validate_email_code_api(page, code)
                self.log("[邮箱] 已通过 Camoufox 浏览器会话 Sentinel 接口提交邮箱验证码")
                if continue_url:
                    page.goto(continue_url, wait_until="domcontentloaded", timeout=90000)
                self._wait_after_otp_submit(page)
                return
            try:
                continue_url = self._validate_email_code_api(page, code)
                self.log("[邮箱] 已通过 JSON 接口提交邮箱验证码")
                if continue_url:
                    page.goto(continue_url, wait_until="domcontentloaded", timeout=90000)
                self._wait_after_otp_submit(page)
                return
            except RuntimeError as exc:
                if self._is_cloudflare_challenge(str(exc)):
                    raise
                self.log(f"[邮箱] JSON 接口提交邮箱验证码未完成，改用页面提交兜底：{str(exc)[:220]}")
            if self._submit_email_code_form(page):
                self.log("[邮箱] 已通过页面原生表单提交邮箱验证码")
                try:
                    self._wait_after_otp_submit(page)
                except RuntimeError as exc:
                    detail = str(exc)
                    if not self._is_email_otp_html_route_error(detail):
                        raise
                    self.log(f"[邮箱] 页面原生验证码提交返回 HTML 路由错误，关键请求：{self._email_otp_network_summary(journal)}")
                    self.log("[邮箱] 页面原生验证码提交返回 HTML 路由错误，尝试恢复验证码页并二次页面提交")
                    if self._retry_email_code_page_submit_after_route_error(page, code):
                        return
                    if self.headless:
                        raise RuntimeError(f"后台浏览器验证码页面提交未完成；已停止调用 EmailOtpValidate 兼容接口以避免触发 Cloudflare。关键请求：{self._email_otp_network_summary(journal)}")
                    self.log("[邮箱] 二次页面提交未恢复，改用兼容 JSON 接口重新提交")
                    continue_url = self._validate_email_code_api(page, code)
                    self.log("[邮箱] 已通过兼容接口提交邮箱验证码")
                    if continue_url:
                        page.goto(continue_url, wait_until="domcontentloaded", timeout=90000)
                    self._wait_after_otp_submit(page)
                return
            self.log("[邮箱] 页面未找到可用的验证码提交控件，使用兼容接口提交")
            continue_url = self._validate_email_code_api(page, code)
            self.log("[邮箱] 已通过兼容接口提交邮箱验证码")
            if continue_url:
                page.goto(continue_url, wait_until="domcontentloaded", timeout=90000)
            self._wait_after_otp_submit(page)
        finally:
            detach_journal()

    def _fill_email_code_inputs(self, page, code: str) -> bool:
        inputs = self._visible_inputs(page, ['input[autocomplete="one-time-code"]', 'input[inputmode="numeric"]', 'input[type="tel"]', 'input[name="code"]'])
        if not inputs:
            return False
        code = str(code)
        typed = False
        try:
            for item in inputs:
                try:
                    item.fill("")
                except Exception:
                    pass
            inputs[0].click(timeout=3000)
            page.keyboard.type(code[:6], delay=45)
            typed = True
            page.wait_for_timeout(180)
        except Exception:
            typed = False
        if len(inputs) >= 6:
            try:
                values = [str(inputs[i].input_value(timeout=700) or "") for i in range(min(6, len(inputs)))]
            except Exception:
                values = []
            if not typed or "".join(values)[:6] != code[:6]:
                for i, ch in enumerate(code[:6]):
                    inputs[i].fill(ch)
        else:
            try:
                current = str(inputs[0].input_value(timeout=700) or "")
            except Exception:
                current = ""
            if not typed or current.strip()[:6] != code[:6]:
                inputs[0].fill(code)
        try:
            page.evaluate("""(code) => {
                const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
                const inputs = Array.from(document.querySelectorAll('input[autocomplete="one-time-code"], input[name="code"], input[inputmode="numeric"], input[type="tel"]')).filter(visible);
                if (!inputs.length) return false;
                if (inputs.length >= 6) {
                    inputs.slice(0, 6).forEach((input, index) => {
                        if (!input.value) input.value = String(code)[index] || '';
                        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: input.value}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                    });
                    inputs[Math.min(5, inputs.length - 1)].focus();
                    return true;
                }
                const input = inputs[0];
                if (!input.value) input.value = String(code);
                input.focus();
                input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: input.value}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""", str(code))
        except Exception:
            pass
        return True

    def _retry_email_code_page_submit_after_route_error(self, page, code: str) -> bool:
        methods = [
            ("Playwright 按钮点击", self._submit_email_code_by_locator),
            ("键盘 Enter", self._submit_email_code_by_keyboard),
            ("页面脚本按钮点击", self._submit_email_code_form),
        ]
        for attempt in range(1, 3):
            for label, submitter in methods:
                self._check_cancelled()
                if not self._recover_email_otp_page_and_fill(page, code):
                    continue
                self.log(f"[邮箱] 已恢复验证码页，执行第 {attempt} 次页面内提交重试：{label}")
                if not submitter(page):
                    continue
                try:
                    self._wait_after_otp_submit(page)
                    self.log(f"[邮箱] 已通过{label}重试提交邮箱验证码")
                    return True
                except RuntimeError as exc:
                    if not self._is_email_otp_html_route_error(str(exc)):
                        raise
            self._sleep_checked(1)
        return False

    def _recover_email_otp_page_and_fill(self, page, code: str) -> bool:
        if not self._has_otp_input(page):
            try:
                page.go_back(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                try:
                    page.goto(f"{AUTH_BASE_URL}/email-verification", wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
        deadline = time.time() + 12
        while time.time() < deadline and not self._has_otp_input(page):
            self._sleep_checked(0.5)
        return self._fill_email_code_inputs(page, code)

    def _submit_email_code_by_locator(self, page) -> bool:
        selectors = [
            'button[data-dd-action-name="Continue"][type="submit"]',
            'button[type="submit"][name="intent"][value="validate"]',
            'button[type="submit"]:not([value="resend"])',
            'input[type="submit"]:not([value="resend"])',
        ]
        for selector in selectors:
            try:
                target = page.locator(selector).first
                if target.is_visible(timeout=800):
                    target.scroll_into_view_if_needed(timeout=3000)
                    target.click(timeout=8000)
                    return True
            except Exception:
                pass
        return False

    def _attach_email_otp_network_journal(self, page):
        journal: list[str] = []

        def interesting(url: str) -> bool:
            value = str(url or "")
            return (
                "auth.openai.com" in value
                and (
                    "email-otp" in value
                    or "email-verification" in value
                    or "route" in value.lower()
                    or "sign-in" in value
                )
            )

        def remember(item: str) -> None:
            journal.append(item)
            del journal[:-12]

        def on_request(request) -> None:
            try:
                url = request.url
                if interesting(url):
                    remember(f"REQ {request.method} {url[:180]}")
            except Exception:
                pass

        def on_response(response) -> None:
            try:
                url = response.url
                if interesting(url):
                    ctype = ""
                    try:
                        ctype = response.headers.get("content-type", "")
                    except Exception:
                        pass
                    remember(f"RESP {response.status} {ctype[:60]} {url[:160]}")
                    if int(response.status or 0) >= 400:
                        try:
                            body = str(response.text() or "")
                            body = re.sub(r"\b\d{6}\b", "<redacted-code>", body)
                            body = re.sub(
                                r'("(?:access_token|refresh_token|id_token|token)"\s*:\s*")[^"]+',
                                r'\1<redacted>',
                                body,
                                flags=re.I,
                            )
                            if body.strip():
                                remember(f"RESPBODY {body.strip()[:500]}")
                        except Exception:
                            pass
            except Exception:
                pass

        def on_request_failed(request) -> None:
            try:
                url = request.url
                if interesting(url):
                    failure = request.failure or ""
                    remember(f"FAIL {request.method} {url[:160]} {failure}")
            except Exception:
                pass

        try:
            page.on("request", on_request)
            page.on("response", on_response)
            page.on("requestfailed", on_request_failed)
        except Exception:
            pass

        def detach() -> None:
            try:
                page.remove_listener("request", on_request)
                page.remove_listener("response", on_response)
                page.remove_listener("requestfailed", on_request_failed)
            except Exception:
                pass

        return journal, detach

    def _email_otp_network_summary(self, journal: list[str]) -> str:
        if not journal:
            return "未捕获到关键请求"
        return " | ".join(journal[-6:])[:900]

    def _email_otp_validation_was_sent(self, journal: list[str]) -> bool:
        return any(
            item.startswith("REQ POST ") and ("email-otp" in item or "email-verification" in item)
            for item in journal
        )

    def _is_invalid_email_otp_error(self, text: str) -> bool:
        value = str(text or "").lower()
        markers = (
            "incorrect code",
            "invalid code",
            "wrong code",
            "不正确的代码",
            "验证码错误",
            "无效验证码",
            "不正確なコード",
            "コードが正しくありません",
        )
        return any(marker.lower() in value for marker in markers)

    def _reset_email_otp_submit_guard(self, page) -> None:
        try:
            page.evaluate("""() => {
                document.querySelectorAll('[data-sunny-register-submitted]').forEach(el => {
                    delete el.dataset.sunnyRegisterSubmitted;
                });
            }""")
        except Exception:
            pass

    def _click_resend_email_code(self, page) -> bool:
        selectors = [
            'button[type="submit"][name="intent"][value="resend"]',
            'button[type="submit"][value="resend"]',
            'input[type="submit"][value="resend"]',
            '[data-dd-action-name*="Resend" i]',
        ]
        for selector in selectors:
            try:
                target = page.locator(selector).first
                if target.is_visible(timeout=800):
                    target.click(timeout=8000)
                    return True
            except Exception:
                pass
        try:
            return bool(page.evaluate("""() => {
                const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
                const items = Array.from(document.querySelectorAll('button,input[type="submit"],[role="button"]')).filter(visible);
                const target = items.find(el => /resend|send again|重新发送|再送信|メールを再送信/i.test(`${el.value || ''} ${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`));
                if (!target || target.disabled || target.getAttribute('aria-disabled') === 'true') return false;
                target.click();
                return true;
            }"""))
        except Exception:
            return False

    def _retry_with_fresh_email_code(self, page, previous_code: str) -> None:
        requested_at = time.time() - 2
        if not self._click_resend_email_code(page):
            raise RuntimeError("邮箱验证码无效，且页面未提供可用的重新发送按钮；请等待几分钟后重新发起任务")
        self.log("[邮箱] 已请求新的 OpenAI 邮箱验证码")
        fresh_code = self.otp_reader.wait_for_code(requested_at, 150)
        if str(fresh_code) == str(previous_code):
            raise RuntimeError("邮箱服务返回了与已拒绝验证码相同的内容；已停止重复提交，请稍后重新发起任务")
        self._reset_email_otp_submit_guard(page)
        if not self._recover_email_otp_page_and_fill(page, fresh_code):
            raise RuntimeError("收到新邮箱验证码，但验证码输入框不可用")
        if not self._submit_email_code_form(page):
            raise RuntimeError("收到新邮箱验证码，但页面提交控件不可用")
        self.log("[邮箱] 已提交新获取的邮箱验证码")
        self._wait_after_otp_submit(page)

    def _submit_email_code_by_keyboard(self, page) -> bool:
        try:
            focused = bool(page.evaluate("""() => {
                const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
                const inputs = Array.from(document.querySelectorAll('input[autocomplete="one-time-code"], input[name="code"], input[inputmode="numeric"], input[type="tel"]')).filter(visible);
                const target = inputs[inputs.length - 1] || inputs[0];
                if (!target) return false;
                target.focus();
                return true;
            }"""))
            if not focused:
                return False
            page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    def _submit_email_code_form(self, page) -> bool:
        if self._submit_email_code_by_locator(page):
            return True
        try:
            return bool(page.evaluate("""() => {
                const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
                const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true' && !el.hasAttribute('disabled');
                const otpInput = Array.from(document.querySelectorAll('input[autocomplete="one-time-code"], input[name="code"], input[inputmode="numeric"]')).find(visible);
                if (!otpInput) return false;
                const form = otpInput.form || otpInput.closest('form');
                const scope = form || document;
                const selectors = [
                    'button[data-dd-action-name="Continue"][type="submit"]',
                    'button[type="submit"][name="intent"][value="validate"]',
                    'button[type="submit"]:not([value="resend"])',
                    'input[type="submit"]:not([value="resend"])'
                ];
                let submitter = null;
                for (const selector of selectors) {
                    submitter = Array.from(scope.querySelectorAll(selector)).find(el => {
                        const identity = `${el.value || ''} ${el.name || ''} ${el.id || ''} ${el.getAttribute('data-dd-action-name') || ''} ${el.textContent || ''}`;
                        return visible(el) && enabled(el) && !/resend|cancel|back|重新发送|取消|返回|キャンセル|再送信/i.test(identity);
                    });
                    if (submitter) break;
                }
                if (!submitter) return false;
                if (submitter.dataset.sunnyRegisterSubmitted === 'true' || form?.dataset.sunnyRegisterSubmitted === 'true') return false;
                submitter.dataset.sunnyRegisterSubmitted = 'true';
                if (form) form.dataset.sunnyRegisterSubmitted = 'true';
                submitter.scrollIntoView({block:'center', inline:'center'});
                submitter.focus?.();
                if (typeof submitter.click === 'function') {
                    submitter.click();
                } else if (form && typeof form.requestSubmit === 'function') {
                    form.requestSubmit(submitter);
                } else {
                    return false;
                }
                return true;
            }"""))
        except Exception:
            return False

    def _validate_email_code_api(self, page, code: str) -> str:
        last_detail = ""
        for attempt in range(3):
            self._check_cancelled()
            try:
                user_agent = str(page.evaluate("() => navigator.userAgent") or "").strip() or self.fingerprint.user_agent
            except Exception:
                user_agent = self.fingerprint.user_agent
            device_id = self.device_id or str(uuid.uuid4())
            self.device_id = device_id
            sentinel_token = ""
            try:
                sentinel_token = build_sentinel_token(page, device_id, "email_otp_validate", user_agent)
            except Exception as exc:
                self.log(f"[认证] Sentinel token 生成失败，将保留页面原生会话继续校验：{str(exc)[:220]}")
            headers = {
                "accept": "application/json",
                "accept-language": self.fingerprint.accept_language,
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": str(page.url or f"{AUTH_BASE_URL}/email-verification"),
                "oai-device-id": device_id,
                **generate_datadog_trace_headers(),
            }
            if sentinel_token:
                headers["openai-sentinel-token"] = sentinel_token
            result = browser_fetch(
                page,
                f"{AUTH_BASE_URL}/api/accounts/email-otp/validate",
                method="POST",
                headers=headers,
                body=json.dumps({"code": code}),
            )
            if result.get("ok"):
                payload = result.get("data") or {}
                return str(payload.get("continue_url") or payload.get("page", {}).get("payload", {}).get("url") or "")
            token_label = "sentinel=yes" if sentinel_token else "sentinel=no"
            last_detail = f"HTTP {result.get('status') or 0} {token_label} {str(result.get('text') or '')}"
            lowered = last_detail.lower()
            if "max_check_attempts" in lowered or "too many tries" in lowered:
                raise RuntimeError("邮箱验证码尝试次数已达上限；请等待几分钟后重新发起任务，系统不会继续重复提交验证码")
            if (
                "incorrect code" in lowered
                or "invalid code" in lowered
                or "不正確なコード" in last_detail
                or "验证码错误" in last_detail
            ):
                raise RuntimeError("邮箱验证码被 OpenAI 拒绝；系统已停止重复提交该验证码")
            if self._is_cloudflare_challenge(last_detail) and attempt < 2:
                self.log("[认证] EmailOtpValidate 触发 Cloudflare challenge，打开验证页")
                self._handle_cloudflare_challenge(page, last_detail)
                continue
            status = int(result.get("status") or 0)
            if (status == 429 or status >= 500) and attempt < 2:
                self._sleep_checked(1)
                continue
            break
        if self._is_cloudflare_challenge(last_detail):
            raise RuntimeError("EmailOtpValidate was blocked by Cloudflare; change proxy or use visible browser to pass challenge")
        raise RuntimeError(f"EmailOtpValidate failed: {last_detail[:800]}")

    def _is_email_otp_html_route_error(self, text: str) -> bool:
        value = str(text or "")
        return (
            "Invalid content type: text/html" in value
            or ("Route Error" in value and "text/html" in value)
            or ("不明なエラー" in value and "text/html" in value)
        )

    def _wait_after_otp_submit(self, page, timeout: int = 30) -> None:
        start = time.time()
        while time.time() - start < timeout:
            summary = self._page_text_summary(page)
            if self._is_email_otp_html_route_error(summary):
                raise RuntimeError(f"Email verification route error after OTP submit: {summary}")
            if self._page_needs_manual_attention(page):
                if self.headless:
                    raise RuntimeError("后台浏览器模式遇到 Cloudflare/人机验证；页面原生表单提交已完成，但服务仍要求交互式验证。请更换代理后重试，或切换为可视浏览器模式处理验证")
                self.log("[认证] 邮箱验证码提交后出现交互式验证，请在当前可视浏览器中完成")
                self._sleep_checked(2)
                continue
            if self._has_chatgpt_session(page) or self._context_has_chatgpt_page(page):
                return
            if "about-you" in page.url or self._has_about_you_form(page) or self._has_phone_form(page) or self._has_visible_password(page):
                return
            if not ("email-verification" in page.url or self._has_otp_input(page)):
                return
            self._sleep_checked(1)
        raise RuntimeError(f"Still on email verification page after OTP submit: {self._page_text_summary(page)}")

    def _is_cloudflare_challenge(self, text: str) -> bool:
        value = str(text or "")
        lowered = value.lower()
        return any(
            marker in lowered
            for marker in (
                "cloudflare",
                "challenges.cloudflare.com",
                "__cf_chl",
                "just a moment",
                "sentinel_required",
                "proof_required",
                "challenge_required",
            )
        )

    def _extract_cloudflare_challenge_url(self, text: str) -> str:
        value = unescape(str(text or ""))
        for pattern in [r'cUPMDTk:\s*"([^"]+)"', r'history\.replaceState\([^,]+,[^,]+,"([^"]+)"']:
            match = re.search(pattern, value)
            if match:
                raw = match.group(1).replace("\\/", "/")
                return raw if raw.startswith("http") else f"{AUTH_BASE_URL}{raw}"
        match = re.search(r'https://auth\.openai\.com/[^"\']*__cf_chl[^"\']*', value)
        return match.group(0) if match else ""

    def _has_cloudflare_clearance(self, page) -> bool:
        try:
            return any(cookie.get("name") == "cf_clearance" for cookie in page.context.cookies([AUTH_BASE_URL]))
        except Exception:
            return False

    def _handle_cloudflare_challenge(self, page, challenge_html: str) -> None:
        if self.headless:
            raise RuntimeError("后台浏览器模式遇到 Cloudflare/人机验证，无法在无窗口模式手动处理；请更换代理后重试，或切换为可视浏览器自动模式完成验证")
        challenge_url = self._extract_cloudflare_challenge_url(challenge_html)
        if not challenge_url:
            raise RuntimeError("Cloudflare challenge URL could not be parsed")
        # Keep the flow in the single incognito-style page. Creating another page
        # here makes visible mode look like two browser windows.
        try:
            page.bring_to_front()
        except Exception:
            pass
        try:
            page.goto(challenge_url, wait_until="domcontentloaded", timeout=90000)
            started = time.time()
            last_notice = 0.0
            while time.time() - started < 120:
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                if self._has_cloudflare_clearance(page):
                    self.log("[认证] Cloudflare challenge 已通过，重试邮箱验证码提交")
                    return
                if time.time() - last_notice >= 10:
                    remain = max(0, int(120 - (time.time() - started)))
                    self.log(f"[认证] 等待 Cloudflare 通过，剩余约 {remain}s")
                    last_notice = time.time()
                self._sleep_checked(2)
            raise RuntimeError("Cloudflare challenge was not cleared within 120 seconds")
        finally:
            try:
                page.bring_to_front()
                page.goto(f"{AUTH_BASE_URL}/email-verification", wait_until="domcontentloaded", timeout=90000)
            except Exception:
                pass

    def _has_visible_password(self, page) -> bool:
        return bool(self._visible_inputs(page, ['input[type="password"]', 'input[name="password"]']))

    def _fill_password_step(self, page) -> None:
        login_page = "/log-in/password" in str(page.url or "") or self.existing_account
        password = self.account.chatgpt_password
        if not password and login_page:
            raise RuntimeError("ChatGPT password login is required, but no ChatGPT password is configured")
        if not password:
            password = self._generate_password()
            self.generated_password = password
        self.account.chatgpt_password = password
        self.auth_action = "login" if login_page else "register"
        self.log("[认证] 账号需要密码步骤，已填写 ChatGPT 密码")
        inputs = self._visible_inputs(page, ['input[type="password"]', 'input[name="password"]'])
        if not inputs:
            raise RuntimeError("Entered password step but password input was not found")
        for item in inputs:
            item.fill(password)
        if not self._click_continue(page):
            raise RuntimeError("Password has been filled, but continue button was not found")

    def _switch_password_to_email_code(self, page) -> bool:
        selectors = [
            'button:has-text("Email code")', 'button:has-text("verification code")',
            '[role="button"]:has-text("Email code")', 'a:has-text("Email code")',
            'button:has-text("邮箱验证码")', 'a:has-text("邮箱验证码")',
        ]
        for selector in selectors:
            try:
                target = page.locator(selector).first
                if target.is_visible(timeout=700):
                    target.click(timeout=5000)
                    self.log("[认证] 已切换为邮箱验证码登录")
                    return True
            except Exception:
                continue
        return False

    def _generate_password(self) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        return "".join(random.choice(alphabet) for _ in range(13)) + "!A7"

    def _has_totp_challenge(self, page) -> bool:
        try:
            value = f"{page.url} {page.locator('body').inner_text(timeout=700)}".lower()
        except Exception:
            value = str(getattr(page, "url", "") or "").lower()
        return "mfa-challenge" in value or "authenticator app" in value or "two-factor" in value or "2fa" in value

    def _submit_totp_challenge(self, page) -> None:
        if not self.account.totp_secret:
            raise RuntimeError("2FA is required, but no TOTP secret is configured")
        inputs = self._visible_inputs(page, ['input[autocomplete="one-time-code"]', 'input[name="code"]', 'input[inputmode="numeric"]'])
        if not inputs:
            raise RuntimeError("2FA challenge is visible, but the TOTP input was not found")
        inputs[0].fill(generate_totp(self.account.totp_secret))
        if not self._click_continue(page):
            raise RuntimeError("TOTP was filled, but the verify button was not found")
        self.log("[认证] 已提交 2FA TOTP 验证码")

    def _has_workspace_selection(self, page) -> bool:
        try:
            path = urlparse(str(page.url or "")).path.rstrip("/")
            if path == "/workspace":
                return True
            text = page.locator("body").inner_text(timeout=700).lower()
            return "choose a workspace" in text or "select a workspace" in text
        except Exception:
            return False

    def _select_first_workspace(self, page) -> None:
        selectors = ['input[type="radio"]', '[role="radio"]', '[data-testid*="workspace"]', 'button[data-workspace-id]']
        for selector in selectors:
            for item in self._visible_inputs(page, [selector]):
                try:
                    label = str(item.get_attribute("aria-label") or item.inner_text(timeout=500) or "")
                    if re.search(r"back|cancel|sign out", label, flags=re.I):
                        continue
                    item.click()
                    self._click_continue(page)
                    self.log("[认证] 已选择首个可用 workspace")
                    return
                except Exception:
                    continue
        if self._click_continue(page):
            self.log("[认证] workspace 页面无显式选项，已继续授权流程")
            return
        raise RuntimeError("Workspace selection is required, but no selectable workspace was found")

    def _has_about_you_form(self, page) -> bool:
        try:
            text = page.locator("body").inner_text(timeout=1000).lower()
            if not any(x in text for x in ["about you", "birth", "full name", "finish creating account", "tell us about", "age"]):
                return False
            return len(self._visible_inputs(page, ['input', 'textarea', '[contenteditable="true"]'])) >= 2
        except Exception:
            return False

    def _fill_about_you(self, page) -> None:
        self.auth_action = "register"
        name, birthdate = random_profile()
        birth_year = birthdate.split("-")[0]
        age = str(max(18, datetime.now(timezone.utc).year - int(birth_year)))
        second_context = self._about_you_second_field_context(page)
        second_kind = self._about_you_second_field_kind_from_context(second_context)
        second_value = self._about_you_second_field_value(second_kind, birth_year, age, birthdate, second_context)
        self.log(f"[认证] 填写基础资料: {name} / birthdate={birthdate} / birth_year={birth_year} / age={age} / field={second_kind} / value={second_value}")
        controls = self._visible_inputs(page, ['input', 'textarea', '[contenteditable="true"]'])
        if len(controls) < 2:
            raise RuntimeError("Profile page missing name/age inputs")
        self._force_fill(controls[0], name)
        self._force_fill(controls[1], second_value)
        self._sleep_checked(1.2)
        if not self._click_continue(page):
            raise RuntimeError("Profile filled, but finish button was not found")

    def _about_you_second_field_context(self, page) -> str:
        try:
            return str(page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const controls = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(visible);
                    const el = controls[1];
                    if (!el) return document.body?.innerText || document.title || '';
                    const parts = [
                        `name=${el.getAttribute('name') || ''}`, `id=${el.id || ''}`,
                        `placeholder=${el.getAttribute('placeholder') || ''}`,
                        `aria-label=${el.getAttribute('aria-label') || ''}`,
                        `autocomplete=${el.getAttribute('autocomplete') || ''}`,
                        `inputmode=${el.getAttribute('inputmode') || ''}`,
                        `type=${el.getAttribute('type') || el.type || ''}`,
                        `data-testid=${el.getAttribute('data-testid') || ''}`
                    ];
                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        for (const id of labelledBy.split(/\\s+/)) {
                            const labelEl = document.getElementById(id);
                            if (labelEl) parts.push(labelEl.textContent || '');
                        }
                    }
                    for (const label of Array.from(document.querySelectorAll('label'))) {
                        if (label.htmlFor && label.htmlFor === el.id) parts.push(label.textContent || '');
                        if (label.contains(el)) parts.push(label.textContent || '');
                    }
                    let node = el.parentElement;
                    for (let i = 0; node && i < 3; i += 1, node = node.parentElement) parts.push(node.textContent || '');
                    parts.push(document.querySelector('h1,h2')?.textContent || '');
                    parts.push(document.body?.innerText || '');
                    parts.push(document.title || '');
                    return parts.filter(Boolean).join(' ');
                }"""
            ) or "")
        except Exception:
            return ""

    def _about_you_second_field_kind_from_context(self, context: str) -> str:
        text = re.sub(r"\s+", " ", str(context or "")).strip().lower()
        birth_date_patterns = [
            r"date\s*of\s*birth", r"birthdate", r"\bdob\b", r"fecha\s+de\s+nacimiento", r"\bnacimiento\b",
            r"\bgeburtstag\b", r"\bgeburtsdatum\b", r"生年月日", r"誕生日", r"生年月", r"出生日期", r"出生年月日",
            r"\bdd\s*[/.-]\s*mm\s*[/.-]\s*(yyyy|aaaa)\b", r"\btt\s*\.\s*mm\s*\.\s*jjjj\b",
            r"\bmm\s*[/.-]\s*dd\s*[/.-]\s*yyyy\b", r"\byyyy\s*[/.-]\s*mm\s*[/.-]\s*dd\b", r"type=date",
        ]
        birth_year_patterns = [
            r"birth\s*year", r"year\s*of\s*birth", r"born\s*year", r"生年", r"出生年", r"生まれた年", r"出生年份",
        ]
        age_patterns = [
            r"\bage\b", r"how\s*old", r"年齢", r"歳", r"何歳", r"年龄", r"年纪",
        ]
        if any(re.search(pattern, text, flags=re.I) for pattern in birth_date_patterns):
            return "birth_date"
        if any(re.search(pattern, text, flags=re.I) for pattern in birth_year_patterns):
            return "birth_year"
        if any(re.search(pattern, text, flags=re.I) for pattern in age_patterns):
            return "age"
        if re.search(r"\b(age|年齢|年龄)\b", text, flags=re.I):
            return "age"
        return "age"

    def _about_you_second_field_value(self, second_kind: str, birth_year: str, age: str, birthdate: str, context: str = "") -> str:
        if second_kind == "birth_year":
            return str(birth_year)
        if second_kind == "birth_date":
            return self._format_about_you_birth_date(birthdate, context)
        return str(age)

    def _format_about_you_birth_date(self, birthdate: str, context: str = "") -> str:
        year, month, day = [int(x) for x in str(birthdate).split("-")[:3]]
        text = re.sub(r"\s+", " ", str(context or "")).strip().lower()
        if re.search(r"\bdd\s*[/.-]\s*mm\s*[/.-]\s*(yyyy|aaaa)\b", text) or "fecha de nacimiento" in text:
            return f"{day:02d}/{month:02d}/{year:04d}"
        if re.search(r"\bmm\s*[/.-]\s*dd\s*[/.-]\s*yyyy\b", text):
            return f"{month:02d}/{day:02d}/{year:04d}"
        if re.search(r"\btt\s*\.\s*mm\s*\.\s*jjjj\b", text) or "geburtstag" in text or "geburtsdatum" in text:
            return f"{day:02d}.{month:02d}.{year:04d}"
        return f"{year:04d}-{month:02d}-{day:02d}"

    def _force_fill(self, locator, value: str) -> None:
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
            locator.fill(value, timeout=5000)
            return
        except Exception:
            pass
        locator.evaluate("""(el, value) => {
            el.focus();
            const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(el, value); else el.value = value;
            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""", value)

    def _about_you_current_values_ok(self, page) -> bool:
        try:
            values = [str(x or "").strip() for x in page.evaluate("""() => Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"]')).filter(el => { const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; }).map(el => el.isContentEditable ? el.textContent : el.value)""")]
            nonempty = [x for x in values if x]
            if len(nonempty) < 2:
                return False
            second = nonempty[1]
            kind = self._about_you_second_field_kind_from_context(self._about_you_second_field_context(page))
            if kind == "age":
                return bool(re.fullmatch(r"\d{1,3}", second) and 13 <= int(second) <= 120)
            if kind == "birth_year":
                return bool(re.fullmatch(r"\d{4}", second) and 1900 <= int(second) <= datetime.now(timezone.utc).year - 13)
            return True
        except Exception:
            return False

    def _has_phone_form(self, page) -> bool:
        return bool(self._visible_inputs(page, ['input[type="tel"]', 'input[inputmode="tel"]', 'input[name*="phone" i]', 'input[autocomplete*="tel" i]']))

    def _looks_like_phone_code_page(self, page) -> bool:
        try:
            text = page.locator("body").inner_text(timeout=1000)
        except Exception:
            text = ""
        return bool(re.search(r"SMS|text message|phone number|\+\d", text, flags=re.I) and re.search(r"code|verification", text, flags=re.I))

    def _phone_number_was_rejected(self, page) -> bool:
        summary = self._page_text_summary(page, 500).lower()
        return any(
            phrase in summary
            for phrase in (
                "invalid phone number",
                "phone number is invalid",
                "phone number is not available",
                "phone number is not supported",
                "unable to use this phone number",
                "couldn't verify this phone number",
                "try another phone number",
                "別の電話番号",
                "電話番号は利用できません",
                "無効な電話番号",
            )
        )

    @staticmethod
    def _country_locator_details(locator) -> tuple[str, list[str]]:
        try:
            text_value = str(locator.inner_text(timeout=800) or "").strip()
        except Exception:
            text_value = ""
        values: list[str] = []
        for attribute in (
            "value",
            "data-value",
            "aria-label",
            "aria-valuetext",
            "title",
            "data-country",
            "data-country-code",
            "data-country-iso",
        ):
            try:
                value = str(locator.get_attribute(attribute, timeout=500) or "").strip()
            except Exception:
                value = ""
            if value and value not in values:
                values.append(value)
        try:
            images = locator.locator("img")
            for index in range(min(images.count(), 3)):
                alt = str(images.nth(index).get_attribute("alt", timeout=500) or "").strip()
                if alt and alt not in values:
                    values.append(alt)
        except Exception:
            pass
        return text_value, values

    @staticmethod
    def _country_selects(page) -> list[Any]:
        selects: list[Any] = []
        for selector in (
            'select[aria-label*="country" i]',
            'select[name*="country" i]',
            'select[id*="country" i]',
            'select[class*="country" i]',
            'select[name*="dial" i]',
            'select[aria-label*="phone" i]',
        ):
            try:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 20)):
                    selects.append(locator.nth(index))
            except Exception:
                continue
        return selects

    @staticmethod
    def _visible_country_options(page, trigger=None) -> list[Any]:
        options: list[Any] = []
        scopes: list[Any] = []
        if trigger is not None:
            try:
                controls = str(trigger.get_attribute("aria-controls", timeout=500) or "").strip()
            except Exception:
                controls = ""
            if controls:
                escaped = controls.replace("\\", "\\\\").replace('"', '\\"')
                try:
                    scopes.append(page.locator(f'[id="{escaped}"]'))
                except Exception:
                    pass
        scopes.append(page)
        for selector in (
            '[role="option"]',
            '[role="listbox"] button',
            '[role="menuitem"]',
            '[role="menuitemradio"]',
            '[data-radix-collection-item]',
            '[data-value]',
        ):
            for scope in scopes:
                try:
                    locator = scope.locator(selector)
                    count = min(locator.count(), 300)
                except Exception:
                    continue
                for index in range(count):
                    try:
                        item = locator.nth(index)
                        if item.is_visible():
                            options.append(item)
                    except Exception:
                        continue
        return options

    @staticmethod
    def _native_country_matches(select, context: dict[str, Any]) -> bool:
        values: list[str] = []
        try:
            value = str(select.input_value(timeout=1000) or "").strip()
            if value:
                values.append(value)
        except Exception:
            pass
        text = ""
        try:
            selected = select.locator("option:checked")
            if selected.count():
                text, selected_values = OpenAIEmailRegisterFlow._country_locator_details(selected.nth(0))
                values.extend(selected_values)
        except Exception:
            pass
        score, matched_dial = _country_option_score(text, values, context)
        return score >= 100 or matched_dial == str(context.get("dial_code") or "")

    @staticmethod
    def _custom_country_matches(trigger, option, context: dict[str, Any]) -> bool:
        text, values = OpenAIEmailRegisterFlow._country_locator_details(trigger)
        score, matched_dial = _country_option_score(text, values, context)
        if score >= 100 or matched_dial == str(context.get("dial_code") or ""):
            return True
        try:
            selected = str(option.get_attribute("aria-selected", timeout=500) or "").lower()
            checked = str(option.get_attribute("data-state", timeout=500) or "").lower()
            return selected == "true" or checked in {"checked", "selected"}
        except Exception:
            return False

    def _phone_country_triggers(self, page) -> list[Any]:
        triggers = self._visible_inputs(page, [
            'button[role="combobox"]',
            '[role="combobox"]',
            'button[aria-haspopup="listbox"]',
            'button[aria-haspopup="menu"]',
            '[data-testid*="country" i]',
            '[data-slot="select-trigger"]',
            'button[aria-label*="country" i]',
            'button[aria-label*="dial" i]',
            'button[class*="country" i]',
            'button:has-text("+1")',
        ])
        phone_inputs = self._visible_inputs(
            page,
            ['input[type="tel"]', 'input[inputmode="tel"]', 'input[name*="phone" i]', 'input[autocomplete*="tel" i]'],
        )
        for phone_input in phone_inputs:
            for selector in (
                'xpath=preceding::*[self::button or @role="combobox" or @aria-haspopup="listbox" or @aria-haspopup="menu"][1]',
                'xpath=ancestor::*[.//*[self::button or @role="combobox" or @aria-haspopup="listbox" or @aria-haspopup="menu"]][1]//*[self::button or @role="combobox" or @aria-haspopup="listbox" or @aria-haspopup="menu"]',
            ):
                try:
                    nearby = phone_input.locator(selector)
                    for index in range(min(nearby.count(), 10)):
                        item = nearby.nth(index)
                        if item.is_visible() and item not in triggers:
                            triggers.append(item)
                except Exception:
                    continue
        return triggers

    def _best_country_locator(self, locators: list[Any], context: dict[str, Any]) -> tuple[Any | None, str, str]:
        best = None
        best_score = 0
        best_dial = ""
        best_label = ""
        for locator in locators:
            text_value, values = self._country_locator_details(locator)
            score, matched_dial = _country_option_score(text_value, values, context)
            if score > best_score:
                best = locator
                best_score = score
                best_dial = matched_dial
                best_label = text_value or next(iter(values), "")
        return best, best_dial, best_label

    def _select_phone_country(self, page, phone: dict[str, Any], number: str) -> str:
        context = _phone_country_context(phone, number)
        selected_dial = str(context["dial_code"] or "1")
        for select in self._country_selects(page):
            wanted_iso = str(context.get("country_iso") or "")
            if wanted_iso:
                try:
                    select.select_option(value=wanted_iso, timeout=5000)
                    self._sleep_checked(0.2)
                    if self._native_country_matches(select, context):
                        self.log(f"[接码] 已根据号码前缀 +{selected_dial} 将手机号国家切换为 {wanted_iso}")
                        return selected_dial
                except Exception:
                    pass
            try:
                option_group = select.locator("option")
                options = [option_group.nth(index) for index in range(min(option_group.count(), 300))]
            except Exception:
                continue
            option, matched_dial, label = self._best_country_locator(options, context)
            if option is None:
                continue
            try:
                value = option.get_attribute("value", timeout=500)
                if value is not None:
                    select.select_option(value=value, timeout=5000)
                else:
                    select.select_option(label=option.inner_text(timeout=500), timeout=5000)
                self._sleep_checked(0.2)
                if self._native_country_matches(select, context):
                    selected_dial = matched_dial or selected_dial
                    self.log(f"[接码] 已根据号码前缀 +{selected_dial} 将手机号国家切换为 {label or selected_dial}")
                    return selected_dial
            except Exception:
                continue

        triggers = self._phone_country_triggers(page)
        country_triggers: list[Any] = []
        for trigger in triggers:
            text_value, values = self._country_locator_details(trigger)
            trigger_text = " ".join([text_value, *values]).casefold()
            if any(marker in trigger_text for marker in ("country", "dial", "calling", "+1", "united states", "usa")):
                country_triggers.append(trigger)
        trigger = country_triggers[0] if country_triggers else (triggers[0] if len(triggers) == 1 else None)
        if trigger is not None:
            try:
                trigger.click(timeout=5000, force=True)
                self._sleep_checked(0.3)
                options = self._visible_country_options(page, trigger)
                option, matched_dial, label = self._best_country_locator(options, context)
                if option is None:
                    search_inputs = self._visible_inputs(page, [
                        '[role="listbox"] input',
                        'input[placeholder*="search" i]',
                        'input[aria-label*="country" i]',
                    ])
                    if search_inputs:
                        query = next((hint for hint in context["hints"] if not hint.isdigit()), "")
                        query = query or (f"+{context['dial_code']}" if context["dial_code"] else "")
                        if query:
                            search_inputs[0].fill(query, timeout=3000)
                            self._sleep_checked(0.3)
                            option, matched_dial, label = self._best_country_locator(self._visible_country_options(page, trigger), context)
                if option is not None:
                    option.click(timeout=5000, force=True)
                    self._sleep_checked(0.2)
                    if self._custom_country_matches(trigger, option, context):
                        selected_dial = matched_dial or selected_dial
                        self.log(f"[接码] 已根据号码前缀 +{selected_dial} 将手机号国家切换为 {label or selected_dial}")
                        return selected_dial
            except Exception:
                pass
        if not context["should_select"]:
            return selected_dial
        hints = "/".join(context["hints"]) or "未知国家"
        dial = f"+{context['dial_code']}" if context["dial_code"] else number
        raise RuntimeError(f"无法在手机号页面选择国家：{hints} ({dial})")

    def _handle_phone_if_possible(self, page) -> bool:
        if not self.phone_provider:
            return False
        last_error = ""
        for attempt in range(1, 9):
            inputs = []
            input_deadline = time.time() + 20
            while time.time() < input_deadline:
                inputs = self._visible_inputs(page, ['input[type="tel"]', 'input[inputmode="tel"]', 'input[name*="phone" i]', 'input[autocomplete*="tel" i]'])
                if inputs:
                    break
                self._sleep_checked(0.5)
            if not inputs:
                raise PhoneBindingUnavailableError(f"手机号输入框不可用：{self._page_text_summary(page, 220)}")

            phone = self.phone_provider("next", self.account.email, {})
            if not phone:
                reason = last_error or "所有接码供应商和自建手机号池均无法提供可用手机号"
                raise PhoneBindingUnavailableError(reason)
            number = str(phone.get("number") or "").strip()
            provider_name = str(phone.get("provider_name") or phone.get("provider") or "接码资源")
            try:
                self._emit_progress("phone_started", {"phone_number": number})
                self.log(f"[接码] 第 {attempt} 次手机号绑定尝试，使用 {provider_name}：{number}")
                selected_dial = self._select_phone_country(page, phone, number)
                candidates = _phone_number_candidates(number, selected_dial)
                for idx, candidate in enumerate(candidates):
                    inputs[0].fill(candidate)
                    self._click_continue(page)
                    probe_deadline = time.time() + (18 if idx < len(candidates) - 1 else 60)
                    while time.time() < probe_deadline and not self._looks_like_phone_code_page(page):
                        if self._has_chatgpt_session(page):
                            return True
                        if self._phone_number_was_rejected(page):
                            if idx >= len(candidates) - 1:
                                raise RuntimeError(f"手机号被页面拒绝：{self._page_text_summary(page, 200)}")
                            break
                        self._sleep_checked(1)
                    if self._looks_like_phone_code_page(page):
                        break
                if not self._looks_like_phone_code_page(page):
                    raise RuntimeError(f"手机号提交后未进入验证码页面：{self._page_text_summary(page, 200)}")
                code = self.phone_provider("code", self.account.email, phone)
                if not code:
                    raise RuntimeError("接码供应商未返回短信验证码")
                self._emit_progress("phone_code_received", {"phone_number": number})
                code_inputs = self._visible_inputs(page, ['input[autocomplete="one-time-code"]', 'input[inputmode="numeric"]', 'input[name="code"]'])
                if len(code_inputs) >= 6:
                    for i, ch in enumerate(str(code)[:6]):
                        code_inputs[i].fill(ch)
                elif code_inputs:
                    code_inputs[0].fill(str(code))
                else:
                    raise RuntimeError("未找到短信验证码输入框")
                self._click_continue(page)
                transition_deadline = time.time() + 30
                while time.time() < transition_deadline:
                    current_url = str(page.url or "")
                    if current_url.startswith(DEFAULT_REDIRECT_URI) or self._has_chatgpt_session(page):
                        break
                    if not self._looks_like_phone_code_page(page) and not self._has_phone_form(page):
                        break
                    self._sleep_checked(1)
                if self._looks_like_phone_code_page(page):
                    raise RuntimeError(f"验证码已提交但手机号绑定未完成：{self._page_text_summary(page, 220)}")
                self.phone_provider("success", self.account.email, {**phone, "code": code})
                self.phone_verification_completed = True
                self._emit_progress("phone_bound", {"phone_number": number})
                return True
            except Exception as exc:
                if isinstance(exc, TaskCancelledError) or self.should_cancel():
                    raise
                last_error = f"{provider_name}: {exc}"
                self.phone_provider("bad", self.account.email, {**phone, "error": str(exc)})
                self.log(f"[接码] {last_error}，准备切换下一个接码资源")
                if not self._return_to_phone_entry(page):
                    raise PhoneBindingUnavailableError(last_error) from exc
        raise PhoneBindingUnavailableError(last_error or "接码资源已耗尽")

    def _return_to_phone_entry(self, page) -> bool:
        if self._has_phone_form(page):
            return True
        try:
            clicked = page.evaluate("""() => {
                const visible = el => { const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; };
                const nodes = Array.from(document.querySelectorAll('button,a,[role="button"]')).filter(visible);
                const target = nodes.find(el => /different number|another number|change number|back|返回|更换|其他号码|別の番号/i.test(`${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`));
                if (!target) return false;
                target.click();
                return true;
            }""")
            if clicked:
                deadline = time.time() + 12
                while time.time() < deadline:
                    if self._has_phone_form(page):
                        return True
                    self._sleep_checked(0.5)
        except Exception:
            pass
        try:
            page.go_back(wait_until="domcontentloaded", timeout=30000)
            deadline = time.time() + 12
            while time.time() < deadline:
                if self._has_phone_form(page):
                    return True
                self._sleep_checked(0.5)
        except Exception:
            pass
        return False

    def _has_chatgpt_session(self, page) -> bool:
        pages = [page]
        try:
            pages += [p for p in page.context.pages if p not in pages]
        except Exception:
            pass
        for candidate in pages:
            try:
                if not str(candidate.url or "").startswith(CHATGPT_BASE_URL):
                    continue
                payload = candidate.evaluate("""async () => { const r = await fetch('/api/auth/session', {credentials:'include'}); if (!r.ok) return null; return await r.json(); }""")
                if payload and (payload.get("accessToken") or payload.get("access_token")):
                    return True
            except Exception:
                pass
        return False

    def _context_has_chatgpt_page(self, page) -> bool:
        try:
            return any(str(p.url or "").startswith(CHATGPT_BASE_URL) for p in page.context.pages)
        except Exception:
            return False

    def _extract_session_info(self, context, page) -> dict[str, Any]:
        # Session and OAuth continue in the task's single primary page. Creating
        # a fallback page here can surface as an unexpected second window.
        session_json = self._read_chatgpt_session_json(context, page)
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "")
        if not access_token:
            raise RuntimeError(f"Session JSON missing accessToken: {session_json}")
        storage_state = context.storage_state()
        result = {
            "access_token": access_token,
            "session_json": session_json,
            "storage_state_json": storage_state,
            "phone_bound": self.phone_verification_completed,
            "auth_action": self.auth_action if self.auth_action != "unknown" else "login",
        }
        self._emit_progress("registered", result)
        if not self.require_refresh_token:
            self.log("[Session] 仅注册阶段：已读取 ChatGPT Session，不执行 Codex OAuth / 不获取 Refresh Token")
            return result
        try:
            record = self._authorize_rt_from_browser(context, page)
            result.update({
                "access_token": record.get("access_token") or access_token,
                "refresh_token": record.get("refresh_token") or "",
                "id_token": record.get("id_token") or "",
                "openai_rt": record.get("refresh_token") or "",
                "client_id": record.get("client_id") or DEFAULT_CLIENT_ID,
                "chatgpt_account_id": record.get("account_id") or "",
                "chatgpt_user_id": record.get("chatgpt_user_id") or "",
                "organization_id": record.get("organization_id") or "",
                "plan_type": record.get("plan_type") or "",
                "expires_at": record.get("expires_at") or 0,
                "token_record": record,
            })
            self.log("[Session] 已获取 Access Token 和 Refresh Token")
        except PhoneBindingUnavailableError as exc:
            result["phone_binding_unavailable"] = True
            result["phone_binding_skipped_reason"] = str(exc)
            result["post_registration_error"] = f"手机号接码绑定未完成: {exc}"
            self.log("[接码] 所有接码资源均不可用；已保留 ChatGPT Session，当前账号按已注册状态完成")
        except Exception as exc:
            # Registration/login and Session acquisition have already completed.
            # Keep that usable result instead of turning the whole account into a
            # failed registration when the optional phone/Codex stage fails.
            result["post_registration_error"] = f"已登录 ChatGPT，但获取 Refresh Token 失败: {exc}"
            self.log(f"[Session] {result['post_registration_error']}；已保留 ChatGPT Session，账号状态停留在已完成阶段")
        result["phone_bound"] = bool(result.get("phone_bound")) or self.phone_verification_completed
        return result

    def _read_chatgpt_session_json(self, context, page) -> dict[str, Any]:
        last_error = ""
        for attempt in range(3):
            self._check_cancelled()
            if not self._browser_driver_connected(page):
                raise BrowserDriverDisconnectedError("后台浏览器驱动已断开，无法继续读取 ChatGPT Session")
            try:
                # BrowserContext.request shares the browser cookie jar without
                # navigating the primary page. This is less likely to tear down
                # Camoufox immediately after the account profile was submitted.
                response = context.request.get(
                    f"{CHATGPT_BASE_URL}/api/auth/session",
                    headers={
                        "Accept": "application/json",
                        "Accept-Language": self.fingerprint.accept_language,
                        "Referer": f"{CHATGPT_BASE_URL}/",
                    },
                    timeout=30000,
                )
                body = response.text().strip()
                data = json.loads(body) if body else {}
                if isinstance(data, dict) and (data.get("accessToken") or data.get("access_token")):
                    if attempt:
                        self.log(f"[Session] 第 {attempt + 1} 次读取 ChatGPT Session 成功")
                    return data
                last_error = f"Session API HTTP {response.status}, missing accessToken: {str(data)[:300]}"
            except Exception as exc:
                if _is_browser_driver_disconnected(exc):
                    raise BrowserDriverDisconnectedError(
                        f"后台浏览器驱动已断开，无法继续读取 ChatGPT Session: {exc}"
                    ) from exc
                last_error = str(exc)
            try:
                data = page.evaluate("""async () => {
                    const r = await fetch('https://chatgpt.com/api/auth/session', {credentials:'include'});
                    const text = await r.text();
                    let data = null;
                    try { data = JSON.parse(text); } catch (_) {}
                    return {ok:r.ok, status:r.status, text, data};
                }""")
                payload = data.get("data") if isinstance(data, dict) else None
                if isinstance(payload, dict) and (payload.get("accessToken") or payload.get("access_token")):
                    return payload
                last_error = f"fetch /api/auth/session failed: {str(data)[:300]}"
            except Exception as exc:
                if _is_browser_driver_disconnected(exc):
                    raise BrowserDriverDisconnectedError(
                        f"后台浏览器驱动已断开，无法继续读取 ChatGPT Session: {exc}"
                    ) from exc
                last_error = str(exc)
            self.log(f"[Session] 读取 ChatGPT Session 未成功，准备重试 {attempt + 1}/3：{last_error[:220]}")
            self._sleep_checked(2 + attempt * 2)
        raise RuntimeError(f"Session endpoint did not return valid accessToken after retries: {last_error}")

    def _browser_driver_connected(self, page) -> bool:
        try:
            browser = page.context.browser
            return browser is None or bool(browser.is_connected())
        except Exception as exc:
            return not _is_browser_driver_disconnected(exc)
    def _prepare_browser_oauth_url(self) -> tuple[str, str, str]:
        state = random_urlsafe_string(24)
        code_verifier = random_urlsafe_string(64)
        query = urlencode({
            "client_id": DEFAULT_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "scope": "openid email profile offline_access",
            "state": state,
            "code_challenge": pkce_code_challenge(code_verifier),
            "code_challenge_method": "S256",
            "prompt": "login",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "login_hint": self.account.email,
        })
        return f"{AUTH_BASE_URL}/oauth/authorize?{query}", code_verifier, state

    def _extract_oauth_callback_from_url(self, callback_url: str, expected_state: str = "") -> dict[str, str]:
        parsed = urlparse(callback_url)
        qs = dict((k, v[0] if isinstance(v, list) else v) for k, v in parse_qs(parsed.query).items())
        oauth_error = str(qs.get("error_description") or qs.get("error") or "").strip()
        if oauth_error:
            raise RuntimeError(f"OAuth callback returned an error: {oauth_error}")
        code = str(qs.get("code") or "").strip()
        if not code:
            raise RuntimeError(f"OAuth callback missing code: {callback_url}")
        callback_state = str(qs.get("state") or "").strip()
        if expected_state and callback_state != expected_state:
            raise RuntimeError("OAuth callback state mismatch")
        return {"code": code, "callback_url": callback_url}

    def _click_codex_consent_if_visible(self, page) -> bool:
        try:
            return bool(page.evaluate(r"""() => {
                const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
                const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                const consentForm = Array.from(document.forms).find(form => /\/sign-in-with-chatgpt\/.*\/consent|\/consent(?:[/?#]|$)/i.test(form.action || '')) || null;
                const stableSelectors = [
                    'form[action*="/sign-in-with-chatgpt/"][action*="/consent"] button[data-dd-action-name="Continue"][type="submit"]',
                    'button[data-dd-action-name="Continue"][type="submit"]',
                    'form button[data-dd-action-name="Continue"]',
                    'button[type="submit"][data-dd-action-name="Continue"]',
                    '[data-testid="continue-button"][type="submit"]',
                    '[data-testid="consent-submit"][type="submit"]'
                ];
                let target = null;
                for (const selector of stableSelectors) {
                    target = Array.from(document.querySelectorAll(selector)).find(el => visible(el) && enabled(el));
                    if (target) break;
                }
                const scope = consentForm || document;
                const candidates = Array.from(scope.querySelectorAll('button, [role="button"], input[type="submit"]')).filter(el => visible(el) && enabled(el));
                if (!target) {
                    target = candidates.find(el => /Continue|Allow|Authorize|Approve|同意|继续|授权|批准|続行|許可|承認/i.test(`${el.value || ''} ${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`));
                }
                if (!target && consentForm) {
                    const submitters = candidates.filter(el => {
                        const type = String(el.type || el.getAttribute('type') || '').toLowerCase();
                        const identity = `${el.value || ''} ${el.name || ''} ${el.id || ''} ${el.getAttribute('data-dd-action-name') || ''} ${el.getAttribute('data-testid') || ''} ${el.textContent || ''}`;
                        return type === 'submit' && !/cancel|deny|reject|back|取消|拒绝|キャンセル|戻る/i.test(identity);
                    });
                    if (submitters.length === 1) target = submitters[0];
                }
                if (!target) return false;
                const form = target.form || target.closest('form') || consentForm;
                if (target.dataset.sunnyRegisterSubmitted === 'true' || form?.dataset.sunnyRegisterSubmitted === 'true') return false;
                target.dataset.sunnyRegisterSubmitted = 'true';
                if (form) form.dataset.sunnyRegisterSubmitted = 'true';
                target.scrollIntoView({block:'center', inline:'center'});
                target.focus?.();
                if (form && typeof form.requestSubmit === 'function') {
                    form.requestSubmit(target);
                } else if (typeof target.click === 'function') {
                    target.click();
                } else if (form && typeof form.submit === 'function') {
                    form.submit();
                } else {
                    return false;
                }
                return true;
            }"""))
        except Exception:
            return False

    def _exchange_browser_code_for_token(self, context, code: str, code_verifier: str) -> dict[str, Any]:
        session = requests.Session()
        session.proxies.update(proxy_dict(self.proxy_url))
        form = {
            "grant_type": "authorization_code",
            "client_id": DEFAULT_CLIENT_ID,
            "code": code,
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "code_verifier": code_verifier,
        }
        last_error = ""
        for token_url in AUTH_OAUTH_TOKEN_URLS:
            for attempt in range(1, 4):
                try:
                    response = session.post(
                        token_url,
                        headers=openai_browser_headers({"accept": "application/json", "content-type": "application/x-www-form-urlencoded", "user-agent": "codex-cli/0.91.0"}),
                        data=form,
                        timeout=45,
                    )
                except requests.RequestException as exc:
                    last_error = f"endpoint={token_url} attempt={attempt}/3 network_error={exc}"
                    self.log(f"[Session] Code 换 Token 网络异常，准备重试：{last_error}")
                    if attempt < 3:
                        self._sleep_checked(attempt)
                    continue
                if response.ok:
                    return normalize_auth_record(self.account.email, response.json())
                last_error = f"endpoint={token_url} HTTP {response.status_code} {response.text[:300]}"
                if response.status_code == 429 or response.status_code >= 500:
                    self.log(f"[Session] Code 换 Token 遇到临时响应，准备重试：{last_error}")
                    if attempt < 3:
                        self._sleep_checked(attempt)
                    continue
                break
        raise RuntimeError(f"Code 换 Token 失败: {last_error}")

    def _authorize_rt_from_browser(self, context, page) -> dict[str, Any]:
        oauth_url, code_verifier, expected_state = self._prepare_browser_oauth_url()
        callback_pattern = re.compile(r"^" + re.escape(DEFAULT_REDIRECT_URI) + r"(?:[?#]|$)")
        captured_callback = {"url": ""}

        def remember_callback_url(value: str) -> bool:
            callback_url = str(value or "")
            if not callback_pattern.match(callback_url):
                return False
            captured_callback["url"] = callback_url
            return True

        def capture_callback_request(request) -> None:
            remember_callback_url(getattr(request, "url", ""))

        def capture_callback_navigation(frame) -> None:
            remember_callback_url(getattr(frame, "url", ""))

        def fulfill_callback(route) -> None:
            remember_callback_url(getattr(route.request, "url", ""))
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=(
                    "<!doctype html><html><head><meta charset='utf-8'><title>SunnyRegister</title></head>"
                    "<body style='font-family:system-ui;padding:40px'>"
                    "<h2>Authorization completed</h2><p>You can return to SunnyRegister.</p>"
                    "</body></html>"
                ),
            )

        page.on("request", capture_callback_request)
        page.on("framenavigated", capture_callback_navigation)
        page.route(callback_pattern, fulfill_callback)
        self.log("[Session] 在当前登录态发起 OAuth 授权获取 Refresh Token")
        try:
            _goto_auth_page(page, oauth_url, self.log, timeout=90000)
            started = time.time()
            last_notice = 0.0
            while time.time() - started < 180:
                callback_url = captured_callback["url"]
                current_url = str(page.url or "")
                if callback_url or remember_callback_url(current_url):
                    callback_url = captured_callback["url"]
                    data = self._extract_oauth_callback_from_url(callback_url, expected_state)
                    self.log("[Session] 已捕获 OAuth callback，正在交换 Refresh Token")
                    return self._exchange_browser_code_for_token(context, data["code"], code_verifier)
                if "add-phone" in current_url or "phone-verification" in current_url or self._has_phone_form(page):
                    if self.phone_verification_completed:
                        if self._click_codex_consent_if_visible(page):
                            self.log("[Session] 已自动点击 Codex 授权继续按钮")
                            self._sleep_checked(2)
                            continue
                        self._sleep_checked(1)
                        continue
                    self.log("[Session] OAuth 授权要求手机号验证，开始联动接码配置")
                    if self._handle_phone_if_possible(page):
                        self._sleep_checked(2)
                        continue
                    raise RuntimeError("OAuth phone verification required, but no usable SMS provider is configured")
                if self._click_codex_consent_if_visible(page):
                    self.log("[Session] 已自动点击 Codex 授权继续按钮")
                    self._sleep_checked(2)
                    continue
                if time.time() - last_notice >= 15:
                    remain = max(0, int(180 - (time.time() - started)))
                    self.log(f"[Session] 等待 OAuth callback，剩余约 {remain}s，当前 URL: {current_url[:100]}")
                    last_notice = time.time()
                self._sleep_checked(1)
            raise TimeoutError(f"OAuth 授权 180 秒内未到 callback，当前 URL: {page.url}")
        finally:
            try:
                page.unroute(callback_pattern, fulfill_callback)
            except Exception:
                pass
            try:
                page.remove_listener("request", capture_callback_request)
                page.remove_listener("framenavigated", capture_callback_navigation)
            except Exception:
                pass

    def _page_text_summary(self, page, max_length: int = 300) -> str:
        try:
            text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=1500)).strip()
            return text[:max_length] or str(page.url)
        except Exception:
            return str(page.url)


def login_or_register(account: MailAccount, proxy_url: str = "", headless: bool = True, log: Callable[[str], None] | None = None, phone_provider=None, existing_account: bool = False, require_refresh_token: bool = True, should_cancel: Callable[[], bool] | None = None, execution_mode: str = "", on_progress: Callable[[str, dict[str, Any]], None] | None = None, mailbox_proxy_url: str | None = None) -> dict[str, Any]:
    if should_cancel and should_cancel():
        raise TaskCancelledError("Task cancelled by user")
    if account.openai_rt and require_refresh_token:
        _emit(log, "[Session] 使用已保存 OpenAI RT 刷新 Session")
        if should_cancel and should_cancel():
            raise TaskCancelledError("Task cancelled by user")
        payload = refresh_openai_access_token(account.openai_rt, proxy_url)
        if should_cancel and should_cancel():
            raise TaskCancelledError("Task cancelled by user")
        session = fetch_session(payload["access_token"], proxy_url)
        session.update({
            "refresh_token": payload.get("refresh_token") or account.openai_rt,
            "id_token": payload.get("id_token", ""),
            "openai_rt": payload.get("refresh_token") or account.openai_rt,
            "token_record": normalize_auth_record(account.email, payload),
        })
        if on_progress:
            on_progress("phone_bound", session)
        return session
    return OpenAIEmailRegisterFlow(account, proxy_url, headless, log, phone_provider=phone_provider, existing_account=existing_account, require_refresh_token=require_refresh_token, should_cancel=should_cancel, execution_mode=execution_mode, on_progress=on_progress, mailbox_proxy_url=mailbox_proxy_url).run()
