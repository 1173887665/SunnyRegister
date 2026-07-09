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

from .mailbox import MailAccount, HotmailReader
from .proxy import playwright_proxy, proxy_dict

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

class TaskCancelledError(RuntimeError):
    pass


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
    exp = int(claims.get("exp") or 0)
    return {
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token") or ""),
        "id_token": str(payload.get("id_token") or ""),
        "account_id": _first_text(auth.get("chatgpt_account_id"), auth.get("account_id"), id_auth.get("chatgpt_account_id")),
        "email": _first_text(id_claims.get("email"), claims.get("email"), email),
        "expired": datetime.fromtimestamp(exp, timezone.utc).isoformat().replace("+00:00", "Z") if exp else "",
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


class OpenAIEmailRegisterFlow:
    """SunnyRegister in-project email register/login flow, following the original register-or-login implementation."""

    def __init__(self, account: MailAccount, proxy_url: str, headless: bool, log: Callable[[str], None] | None, phone_provider=None, existing_account: bool = False, require_refresh_token: bool = True, should_cancel: Callable[[], bool] | None = None, execution_mode: str = ""):
        self.account = account
        self.proxy_url = proxy_url
        self.headless = headless
        self.execution_mode = (execution_mode or ("background" if headless else "visible")).strip().lower()
        self.log = log or (lambda _m: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.phone_provider = phone_provider
        self.otp_reader: HotmailReader | None = None
        self.existing_account = existing_account
        self.fingerprint = generate_register_fingerprint()
        self.auth_action = "login" if existing_account else "unknown"
        self.require_refresh_token = require_refresh_token

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

    def run(self) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"Playwright is required for email register/login: {exc}")
        self.log(f"[认证] 开始注册或登录: {self.account.email}")
        with sync_playwright() as pw:
            browser = None
            context = None
            try:
                self._check_cancelled()
                self._preconnect_otp_reader()
                self._check_cancelled()
                mode_label = "后台浏览器自动（Headless，无窗口）" if self.headless else "可视浏览器自动（Visible，有窗口）"
                self.log(f"[认证] 执行方式：{mode_label}")
                browser = pw.chromium.launch(
                    headless=self.headless,
                    proxy=playwright_proxy(self.proxy_url),
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        f"--lang={self.fingerprint.locale}",
                        f"--window-size={self.fingerprint.outer_width},{self.fingerprint.outer_height}",
                        "--disable-features=IsolateOrigins,site-per-process",
                    ],
                )
                # Playwright browser.new_context() is already an isolated, non-persistent
                # incognito-style context. Using a persistent profile with incognito flags can
                # make Chromium open both a normal profile window and an incognito window.
                context = browser.new_context(
                    user_agent=self.fingerprint.user_agent,
                    locale=self.fingerprint.locale,
                    timezone_id=self.fingerprint.timezone,
                    viewport={"width": self.fingerprint.viewport_width, "height": self.fingerprint.viewport_height},
                    screen={"width": self.fingerprint.screen_width, "height": self.fingerprint.screen_height},
                    device_scale_factor=self.fingerprint.device_scale_factor,
                    is_mobile=False,
                    has_touch=False,
                )
                self._install_stealth(context)
                context.clear_cookies()
                self.log(f"[认证] 已启动隔离无痕浏览器上下文，语言环境 {self.fingerprint.locale} / {self.fingerprint.timezone}")
                self.log(f"[认证] 浏览器指纹 Chrome/{self.fingerprint.user_agent.split('Chrome/')[1].split('.')[0]} {self.fingerprint.viewport_width}x{self.fingerprint.viewport_height} {self.fingerprint.locale} {self.fingerprint.timezone} cpu={self.fingerprint.hardware_concurrency} mem={self.fingerprint.device_memory}")
                self._check_cancelled()
                page = context.new_page()
                page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
                self._check_cancelled()
                signin_url = self._create_openai_signin_url(context)
                otp_min_timestamp = time.time() - 10
                page.goto(signin_url, wait_until="domcontentloaded", timeout=90000)
                self.log("[认证] 已打开 OpenAI 认证页；如出现人机验证，请在浏览器中手动完成")
                self._drive_register_or_login(page, otp_min_timestamp)
                result = self._extract_session_info(context, page)
                result["auth_action"] = self.auth_action if self.auth_action != "unknown" else "login"
                self.log("[认证] 注册或登录完成，已读取 Session 信息")
                return result
            finally:
                if self.otp_reader:
                    self.otp_reader.close()
                try:
                    if context:
                        context.close()
                finally:
                    if browser:
                        browser.close()

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
        self.log("[邮箱] 提前连接 Outlook IMAP，准备接收 OpenAI 验证码")
        self.otp_reader = HotmailReader(self.account, self.log, "")
        self.otp_reader.connect()

    def _create_openai_signin_url(self, context) -> str:
        csrf_value, device_id = self._get_chatgpt_csrf_and_device(context)
        if not csrf_value:
            raise RuntimeError("Missing ChatGPT CSRF token; cannot open auth page")
        device_id = device_id or str(uuid.uuid4())
        query = urlencode({
            "prompt": "login",
            "ext-oai-did": device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "ext-passkey-client-capabilities": "0111",
            "screen_hint": "login" if self.existing_account else "signup",
            "login_hint": self.account.email,
            "locale": self.fingerprint.locale,
        })
        response = context.request.post(
            f"{CHATGPT_BASE_URL}/api/auth/signin/openai?{query}",
            form={"callbackUrl": f"{CHATGPT_BASE_URL}/", "csrfToken": csrf_value, "json": "true"},
            headers={"Accept": "application/json", "Accept-Language": self.fingerprint.accept_language},
            timeout=30000,
        )
        if not response.ok:
            raise RuntimeError(f"Waiting for OpenAI email OTP: HTTP {response.status} {response.text()[:300]}")
        payload = response.json()
        signin_url = str(payload.get("url") or "")
        if not signin_url:
            raise RuntimeError(f"Auth response missing redirect URL: {payload}")
        return signin_url

    def _get_chatgpt_csrf_and_device(self, context) -> tuple[str, str]:
        csrf_value = ""
        device_id = ""
        for cookie in context.cookies([CHATGPT_BASE_URL, "https://openai.com"]):
            if cookie.get("name") == "__Host-next-auth.csrf-token":
                csrf_value = unquote(cookie.get("value", "")).split("|")[0]
            if cookie.get("name") == "oai-did":
                device_id = cookie.get("value", "")
        if not csrf_value:
            try:
                response = context.request.get(
                    f"{CHATGPT_BASE_URL}/api/auth/csrf",
                    headers={"Accept": "application/json", "Accept-Language": self.fingerprint.accept_language, "Referer": f"{CHATGPT_BASE_URL}/"},
                    timeout=30000,
                )
                if response.ok:
                    csrf_value = str(response.json().get("csrfToken") or "").strip()
            except Exception as exc:
                self.log(f"[{self.account.email}] CSRF API failed; falling back to cookie: {exc}")
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
            if "password" in url and self._has_visible_password(page):
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
            signin_url = self._create_openai_signin_url(page.context)
            page.goto(signin_url, wait_until="domcontentloaded", timeout=90000)
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
        return True

    def _has_otp_input(self, page) -> bool:
        if self._has_about_you_form(page) or self._looks_like_phone_code_page(page):
            return False
        return bool(self._visible_inputs(page, ['input[autocomplete="one-time-code"]', 'input[name="code"]', 'input[inputmode="numeric"]']))

    def _submit_email_code(self, page, min_timestamp: float) -> None:
        if not self.otp_reader:
            self.otp_reader = HotmailReader(self.account, self.log, "")
        self.log("[邮箱] 等待 OpenAI 邮箱验证码")
        code = self.otp_reader.wait_for_code(min_timestamp, 180)
        inputs = self._visible_inputs(page, ['input[autocomplete="one-time-code"]', 'input[inputmode="numeric"]', 'input[type="tel"]', 'input[name="code"]'])
        if not inputs:
            raise RuntimeError("Email OTP input was not found")
        if len(inputs) >= 6:
            for i, ch in enumerate(code[:6]):
                inputs[i].fill(ch)
        else:
            inputs[0].fill(code)
        continue_url = self._validate_email_code_api(page, code)
        self.log("[邮箱] 已提交邮箱验证码")
        if continue_url:
            page.goto(continue_url, wait_until="domcontentloaded", timeout=90000)
        self._wait_after_otp_submit(page)

    def _validate_email_code_api(self, page, code: str) -> str:
        last_detail = ""
        for attempt in range(3):
            result = page.evaluate("""async ({code}) => {
                const resp = await fetch('/api/accounts/email-otp/validate', {
                    method: 'POST', credentials: 'include',
                    headers: { accept: 'application/json', 'content-type': 'application/json', origin: 'https://auth.openai.com', referer: 'https://auth.openai.com/email-verification' },
                    body: JSON.stringify({ code })
                });
                const text = await resp.text(); let data = null;
                try { data = JSON.parse(text); } catch (_) {}
                return { ok: resp.ok, status: resp.status, text, data };
            }""", {"code": code})
            if result.get("ok"):
                payload = result.get("data") or {}
                return str(payload.get("continue_url") or payload.get("page", {}).get("payload", {}).get("url") or "")
            last_detail = str(result.get("text") or result.get("status") or "")
            if self._is_cloudflare_challenge(last_detail) and attempt < 2:
                self.log("[认证] EmailOtpValidate 触发 Cloudflare challenge，打开验证页")
                self._handle_cloudflare_challenge(page, last_detail)
                continue
            self._sleep_checked(1)
        if self._is_cloudflare_challenge(last_detail):
            raise RuntimeError("EmailOtpValidate was blocked by Cloudflare; change proxy or use visible browser to pass challenge")
        raise RuntimeError(f"EmailOtpValidate failed: {last_detail[:800]}")

    def _wait_after_otp_submit(self, page, timeout: int = 30) -> None:
        start = time.time()
        while time.time() - start < timeout:
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
        return "challenges.cloudflare.com" in value or "__cf_chl" in value or "Just a moment" in value

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
        password = self.account.password or self._generate_password()
        if len(password) < 12:
            password = password + password
        self.account.password = password
        self.log("[认证] 账号需要密码步骤，已填写密码")
        inputs = self._visible_inputs(page, ['input[type="password"]', 'input[name="password"]'])
        if not inputs:
            raise RuntimeError("Entered password step but password input was not found")
        for item in inputs:
            item.fill(password)
        if not self._click_continue(page):
            raise RuntimeError("Password has been filled, but continue button was not found")

    def _generate_password(self) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        return "".join(random.choice(alphabet) for _ in range(13)) + "!A7"

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

    def _handle_phone_if_possible(self, page) -> bool:
        if not self.phone_provider:
            return False
        phone = self.phone_provider("next", self.account.email, {"country": "US"})
        if not phone:
            return False
        number = str(phone.get("number") or "").strip()
        try:
            inputs = self._visible_inputs(page, ['input[type="tel"]', 'input[inputmode="tel"]', 'input[name*="phone" i]', 'input[autocomplete*="tel" i]', 'input[inputmode="numeric"]'])
            if not inputs:
                raise RuntimeError("Phone input was not found on phone verification page")
            self.log(f"[手机] 服务要求电话验证，已填写手机号 {number}")
            digits = re.sub(r"\D", "", number)
            local = digits
            if local.startswith("1") and len(local) > 10:
                local = local[-10:]
            candidates = []
            for item in [local, digits, number]:
                item = str(item or "").strip()
                if item and item not in candidates:
                    candidates.append(item)
            for idx, candidate in enumerate(candidates):
                inputs[0].fill(candidate)
                self._click_continue(page)
                probe_deadline = time.time() + (18 if idx < len(candidates) - 1 else 60)
                while time.time() < probe_deadline and not self._looks_like_phone_code_page(page):
                    if self._has_chatgpt_session(page):
                        return True
                    if "invalid" in self._page_text_summary(page, 180).lower() and idx < len(candidates) - 1:
                        break
                    self._sleep_checked(1)
                if self._looks_like_phone_code_page(page):
                    break
            deadline = time.time() + 60
            while time.time() < deadline and not self._looks_like_phone_code_page(page):
                if self._has_chatgpt_session(page):
                    return True
                self._sleep_checked(1)
            if not self._looks_like_phone_code_page(page):
                raise RuntimeError(f"Phone number submitted but SMS code page did not appear: {self._page_text_summary(page, 200)}")
            code = self.phone_provider("code", self.account.email, phone)
            if not code:
                raise RuntimeError("SMS code input/code was not found")
            code_inputs = self._visible_inputs(page, ['input[autocomplete="one-time-code"]', 'input[inputmode="numeric"]', 'input[name="code"]'])
            if len(code_inputs) >= 6:
                for i, ch in enumerate(str(code)[:6]):
                    code_inputs[i].fill(ch)
            elif code_inputs:
                code_inputs[0].fill(str(code))
            else:
                raise RuntimeError("SMS code input/code was not found")
            self._click_continue(page)
            self.phone_provider("success", self.account.email, {**phone, "code": code})
            return True
        except Exception as exc:
            self.phone_provider("bad", self.account.email, {**phone, "error": str(exc)})
            raise

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

    def _extract_session_info(self, context, page=None) -> dict[str, Any]:
        if page is None:
            page = context.new_page()
        session_json = self._read_chatgpt_session_json(context, page)
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "")
        if not access_token:
            raise RuntimeError(f"Session JSON missing accessToken: {session_json}")
        storage_state = context.storage_state()
        result = {"access_token": access_token, "session_json": session_json, "storage_state_json": storage_state}
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
                "token_record": record,
            })
            self.log("[Session] 已获取 Access Token 和 Refresh Token")
        except Exception as exc:
            raise RuntimeError(f"已登录 ChatGPT，但获取 Refresh Token 失败: {exc}") from exc
        return result

    def _read_chatgpt_session_json(self, context, page) -> dict[str, Any]:
        last_error = ""
        for attempt in range(3):
            try:
                page.goto(f"{CHATGPT_BASE_URL}/api/auth/session", wait_until="domcontentloaded", timeout=60000)
                body = page.locator("body").inner_text(timeout=15000).strip()
                data = json.loads(body)
                if isinstance(data, dict) and (data.get("accessToken") or data.get("access_token")):
                    if attempt:
                        self.log(f"[Session] 第 {attempt + 1} 次读取 ChatGPT Session 成功")
                    return data
                last_error = f"Session JSON missing accessToken: {str(data)[:300]}"
            except Exception as exc:
                last_error = str(exc)
            try:
                if not str(page.url or "").startswith(CHATGPT_BASE_URL):
                    page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
                data = page.evaluate("""async () => {
                    const r = await fetch('/api/auth/session', {credentials:'include'});
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
                last_error = str(exc)
            self.log(f"[Session] 读取 ChatGPT Session 未成功，准备重试 {attempt + 1}/3：{last_error[:220]}")
            self._sleep_checked(2 + attempt * 2)
        raise RuntimeError(f"Session endpoint did not return valid accessToken after retries: {last_error}")
    def _prepare_browser_oauth_url(self) -> tuple[str, str]:
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
        return f"{AUTH_BASE_URL}/oauth/authorize?{query}", code_verifier

    def _extract_oauth_callback_from_url(self, callback_url: str) -> dict[str, str]:
        parsed = urlparse(callback_url)
        qs = dict((k, v[0] if isinstance(v, list) else v) for k, v in parse_qs(parsed.query).items())
        code = str(qs.get("code") or "").strip()
        if not code:
            raise RuntimeError(f"OAuth callback missing code: {callback_url}")
        return {"code": code, "callback_url": callback_url}

    def _click_codex_consent_if_visible(self, page) -> bool:
        try:
            return bool(page.evaluate("""() => {
                const visible = el => { if (!el) return false; const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
                const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"]')).filter(el => visible(el) && enabled(el));
                const target = candidates.find(el => /Continue|Allow|Authorize|Approve|同意|继续|授权|批准/i.test(`${el.value || ''} ${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`));
                if (!target) return false;
                target.scrollIntoView({block:'center', inline:'center'});
                target.click();
                return true;
            }"""))
        except Exception:
            return False

    def _exchange_browser_code_for_token(self, context, code: str, code_verifier: str) -> dict[str, Any]:
        last_error = ""
        for token_url in AUTH_OAUTH_TOKEN_URLS:
            response = context.request.post(
                token_url,
                headers=openai_browser_headers({"accept": "application/json", "content-type": "application/x-www-form-urlencoded"}),
                data={"grant_type": "authorization_code", "client_id": DEFAULT_CLIENT_ID, "code": code, "redirect_uri": DEFAULT_REDIRECT_URI, "code_verifier": code_verifier},
                timeout=30000,
            )
            if response.ok:
                return normalize_auth_record(self.account.email, response.json())
            last_error = f"endpoint={token_url} HTTP {response.status} {response.text()[:300]}"
        raise RuntimeError(f"Code 换 Token 失败: {last_error}")

    def _authorize_rt_from_browser(self, context, page) -> dict[str, Any]:
        oauth_url, code_verifier = self._prepare_browser_oauth_url()
        self.log("[Session] 在当前登录态发起 OAuth 授权获取 Refresh Token")
        page.goto(oauth_url, wait_until="domcontentloaded", timeout=90000)
        started = time.time()
        last_notice = 0.0
        while time.time() - started < 180:
            current_url = str(page.url or "")
            if current_url.startswith(DEFAULT_REDIRECT_URI):
                data = self._extract_oauth_callback_from_url(current_url)
                self.log("[Session] 已获取 OAuth 授权 code，交换 Refresh Token")
                return self._exchange_browser_code_for_token(context, data["code"], code_verifier)
            if "add-phone" in current_url or "phone-verification" in current_url or self._has_phone_form(page):
                self.log("[Session] OAuth 授权要求手机号验证，开始联动接码配置")
                if self._handle_phone_if_possible(page):
                    self._sleep_checked(2)
                    continue
                raise RuntimeError("OAuth phone verification required, but no usable SMS provider is configured")
            if self._click_codex_consent_if_visible(page):
                self._sleep_checked(2)
                continue
            if time.time() - last_notice >= 15:
                remain = max(0, int(180 - (time.time() - started)))
                self.log(f"[Session] 等待 OAuth callback，剩余约 {remain}s，当前 URL: {current_url[:100]}")
                last_notice = time.time()
            self._sleep_checked(1)
        raise TimeoutError(f"OAuth 授权 180 秒内未到 callback，当前 URL: {page.url}")

    def _page_text_summary(self, page, max_length: int = 300) -> str:
        try:
            text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=1500)).strip()
            return text[:max_length] or str(page.url)
        except Exception:
            return str(page.url)


def login_or_register(account: MailAccount, proxy_url: str = "", headless: bool = True, log: Callable[[str], None] | None = None, phone_provider=None, existing_account: bool = False, require_refresh_token: bool = True, should_cancel: Callable[[], bool] | None = None, execution_mode: str = "") -> dict[str, Any]:
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
        return session
    return OpenAIEmailRegisterFlow(account, proxy_url, headless, log, phone_provider=phone_provider, existing_account=existing_account, require_refresh_token=require_refresh_token, should_cancel=should_cancel, execution_mode=execution_mode).run()


