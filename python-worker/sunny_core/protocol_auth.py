from __future__ import annotations

import json
import random
import re
import secrets
import string
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import unquote, urlencode

from .mailbox import HotmailReader, MailAccount
from .proxy import normalize_proxy_url
from .sentinel import (
    SENTINEL_FRAME_URL,
    SENTINEL_REQ_URL,
    SentinelTokenGenerator,
    generate_datadog_trace_headers,
)


AUTH_BASE_URL = "https://auth.openai.com"
CHATGPT_BASE_URL = "https://chatgpt.com"
AUTHORIZE_CONTINUE_URL = f"{AUTH_BASE_URL}/api/accounts/authorize/continue"
REGISTER_PASSWORD_URL = f"{AUTH_BASE_URL}/api/accounts/user/register"
SEND_EMAIL_OTP_URL = f"{AUTH_BASE_URL}/api/accounts/email-otp/send"
VALIDATE_EMAIL_OTP_URL = f"{AUTH_BASE_URL}/api/accounts/email-otp/validate"
CREATE_ACCOUNT_URL = f"{AUTH_BASE_URL}/api/accounts/create_account"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


class ProtocolRegistrationError(RuntimeError):
    pass


class ProtocolChallengeRequired(ProtocolRegistrationError):
    pass


def _json_response(response, step: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        body = str(getattr(response, "text", "") or "")[:500]
        raise ProtocolRegistrationError(f"{step} returned non-JSON content: {body}") from exc
    if not isinstance(payload, dict):
        raise ProtocolRegistrationError(f"{step} returned an invalid JSON object")
    return payload


def _response_error(response, step: str) -> ProtocolRegistrationError:
    status = int(getattr(response, "status_code", 0) or 0)
    body = str(getattr(response, "text", "") or "")[:800]
    marker = body.lower()
    if status in {403, 429} or any(value in marker for value in ("cloudflare", "challenge", "turnstile", "captcha")):
        return ProtocolChallengeRequired(
            f"{step} requires an interactive anti-bot challenge (HTTP {status}): {body}"
        )
    return ProtocolRegistrationError(f"{step} failed (HTTP {status}): {body}")


class ProtocolRegistrationFlow:
    """ChatGPT email registration/login through HTTP requests only.

    The flow owns one TLS-impersonated cookie jar for the complete account
    lifecycle. It never starts Playwright, Chromium, or Camoufox.
    """

    def __init__(
        self,
        account: MailAccount,
        proxy_url: str = "",
        log: Callable[[str], None] | None = None,
        *,
        existing_account: bool = False,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
        session: Any | None = None,
    ):
        self.account = account
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.log = log or (lambda _message: None)
        self.existing_account = existing_account
        self.should_cancel = should_cancel or (lambda: False)
        self.on_progress = on_progress
        self.session = session
        self.reader: HotmailReader | None = None
        self.device_id = ""
        self.auth_url = ""
        self.auth_action = "login" if existing_account else "unknown"
        self.generated_password = ""

    def _check_cancelled(self) -> None:
        if self.should_cancel():
            from .openai_auth import TaskCancelledError

            raise TaskCancelledError("Task cancelled by user")

    def _emit(self, checkpoint: str, data: dict[str, Any] | None = None) -> None:
        if self.on_progress:
            self.on_progress(checkpoint, dict(data or {}))

    def _new_session(self):
        try:
            from curl_cffi import requests as curl_requests
        except Exception as exc:
            raise ProtocolRegistrationError(
                "Protocol mode requires curl_cffi; reinstall python-worker dependencies"
            ) from exc
        proxies = {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None
        session = curl_requests.Session(
            impersonate="chrome136",
            proxies=proxies,
            timeout=30,
        )
        session.headers.update(
            {
                "user-agent": USER_AGENT,
                "accept-language": "ja-JP,ja;q=0.9,en;q=0.7",
                "accept-encoding": "gzip, deflate, br",
            }
        )
        return session

    def _request(self, method: str, url: str, *, step: str, **kwargs):
        self._check_cancelled()
        kwargs.setdefault("timeout", 30)
        try:
            response = self.session.request(method, url, **kwargs)
        except Exception as exc:
            raise ProtocolRegistrationError(f"{step} request failed: {exc}") from exc
        self._check_cancelled()
        return response

    def _cookie(self, name: str) -> str:
        try:
            value = self.session.cookies.get(name)
            return str(value or "")
        except Exception:
            try:
                for cookie in self.session.cookies.jar:
                    if getattr(cookie, "name", "") == name:
                        return str(getattr(cookie, "value", "") or "")
            except Exception:
                pass
        return ""

    def _sentinel_header(self, flow: str) -> str:
        self._check_cancelled()
        generator = SentinelTokenGenerator(self.device_id, USER_AGENT)
        proof = generator.requirements_token()
        response = self._request(
            "POST",
            SENTINEL_REQ_URL,
            step=f"Sentinel {flow}",
            headers={
                "accept": "*/*",
                "content-type": "text/plain;charset=UTF-8",
                "origin": "https://sentinel.openai.com",
                "referer": SENTINEL_FRAME_URL,
            },
            data=json.dumps(
                {"p": proof, "id": self.device_id, "flow": flow},
                separators=(",", ":"),
            ),
        )
        if response.status_code != 200:
            raise _response_error(response, f"Sentinel {flow}")
        payload = _json_response(response, f"Sentinel {flow}")
        challenge = str(payload.get("token") or "").strip()
        if not challenge:
            raise ProtocolRegistrationError(f"Sentinel {flow} did not return a challenge token")
        pow_meta = payload.get("proofofwork") if isinstance(payload.get("proofofwork"), dict) else {}
        if pow_meta.get("required") and pow_meta.get("seed"):
            proof = generator.proof_token(
                str(pow_meta.get("seed") or ""),
                str(pow_meta.get("difficulty") or "0"),
            )
        turnstile = payload.get("turnstile") if isinstance(payload.get("turnstile"), dict) else {}
        if turnstile.get("required") and not turnstile.get("dx"):
            raise ProtocolChallengeRequired(f"Sentinel {flow} requires an interactive Turnstile challenge")
        # Most protocol responses accept an empty t when no interactive widget
        # is required. A server-side rejection is surfaced without browser fallback.
        return json.dumps(
            {
                "p": proof,
                "t": "",
                "c": challenge,
                "id": self.device_id,
                "flow": flow,
            },
            separators=(",", ":"),
        )

    def _start_next_auth(self) -> None:
        landing = self._request(
            "GET",
            f"{CHATGPT_BASE_URL}/",
            step="ChatGPT session initialization",
            allow_redirects=True,
        )
        if landing.status_code >= 500:
            raise _response_error(landing, "ChatGPT session initialization")
        csrf_response = self._request(
            "GET",
            f"{CHATGPT_BASE_URL}/api/auth/csrf",
            step="ChatGPT CSRF initialization",
            headers={"accept": "application/json"},
        )
        if csrf_response.status_code != 200:
            raise _response_error(csrf_response, "ChatGPT CSRF initialization")
        csrf_payload = _json_response(csrf_response, "ChatGPT CSRF initialization")
        csrf_token = str(csrf_payload.get("csrfToken") or "").strip()
        if not csrf_token:
            csrf_cookie = unquote(self._cookie("__Host-next-auth.csrf-token"))
            csrf_token = csrf_cookie.split("|", 1)[0]
        if not csrf_token:
            raise ProtocolRegistrationError("ChatGPT CSRF initialization returned an empty token")
        self.device_id = self._cookie("oai-did") or str(uuid.uuid4())
        query = urlencode(
            {
                "prompt": "login",
                "ext-oai-did": self.device_id,
                "auth_session_logging_id": str(uuid.uuid4()),
                "ext-passkey-client-capabilities": "0111",
                "screen_hint": "login" if self.existing_account else "signup",
                "login_hint": self.account.email,
                "locale": "ja-JP",
            }
        )
        signin = self._request(
            "POST",
            f"{CHATGPT_BASE_URL}/api/auth/signin/openai?{query}",
            step="OpenAI sign-in initialization",
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "origin": CHATGPT_BASE_URL,
                "referer": f"{CHATGPT_BASE_URL}/",
            },
            data={"callbackUrl": f"{CHATGPT_BASE_URL}/", "csrfToken": csrf_token, "json": "true"},
        )
        if signin.status_code != 200:
            raise _response_error(signin, "OpenAI sign-in initialization")
        self.auth_url = str(_json_response(signin, "OpenAI sign-in initialization").get("url") or "")
        if not self.auth_url:
            raise ProtocolRegistrationError("OpenAI sign-in initialization did not return an authorization URL")
        auth_page = self._request(
            "GET",
            self.auth_url,
            step="OpenAI authorization initialization",
            allow_redirects=True,
            headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        if auth_page.status_code >= 400:
            raise _response_error(auth_page, "OpenAI authorization initialization")
        self.device_id = self._cookie("oai-did") or self.device_id

    def _authorize_email(self) -> dict[str, Any]:
        sentinel = self._sentinel_header("authorize_continue")
        response = self._request(
            "POST",
            AUTHORIZE_CONTINUE_URL,
            step="Submit registration email",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": f"{AUTH_BASE_URL}/{'log-in' if self.existing_account else 'create-account'}",
                "oai-device-id": self.device_id,
                "openai-sentinel-token": sentinel,
                **generate_datadog_trace_headers(),
            },
            data=json.dumps(
                {
                    "username": {"value": self.account.email, "kind": "email"},
                    "screen_hint": "login" if self.existing_account else "signup",
                },
                separators=(",", ":"),
            ),
        )
        if response.status_code != 200:
            raise _response_error(response, "Submit registration email")
        self._emit("email_submitted")
        return _json_response(response, "Submit registration email")

    def _password_value(self) -> str:
        value = str(self.account.password or "").strip()
        if len(value) >= 12 and re.search(r"[A-Za-z]", value) and re.search(r"\d", value):
            return value
        alphabet = string.ascii_letters + string.digits + "._!@#"
        required = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase), secrets.choice(string.digits), secrets.choice("._!@#")]
        required.extend(secrets.choice(alphabet) for _ in range(12))
        random.SystemRandom().shuffle(required)
        self.generated_password = "".join(required)
        return self.generated_password

    def _submit_password(self) -> dict[str, Any]:
        self._request(
            "GET",
            f"{AUTH_BASE_URL}/create-account/password",
            step="Load password stage",
            headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        sentinel = self._sentinel_header("username_password_create")
        response = self._request(
            "POST",
            REGISTER_PASSWORD_URL,
            step="Submit account password",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": f"{AUTH_BASE_URL}/create-account/password",
                "oai-device-id": self.device_id,
                "openai-sentinel-token": sentinel,
                **generate_datadog_trace_headers(),
            },
            data=json.dumps(
                {"password": self._password_value(), "username": self.account.email},
                separators=(",", ":"),
            ),
        )
        if response.status_code != 200:
            raise _response_error(response, "Submit account password")
        self.auth_action = "register"
        return _json_response(response, "Submit account password")

    def _wait_for_email_code(self, min_timestamp: float) -> str:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            self._check_cancelled()
            try:
                return self.reader.wait_for_code(min_timestamp, timeout=10)
            except TimeoutError:
                continue
        raise TimeoutError("Timed out waiting for OpenAI email OTP")

    def _verify_email(self, continue_url: str) -> dict[str, Any]:
        verification_url = continue_url or f"{AUTH_BASE_URL}/email-verification"
        page = self._request(
            "GET",
            verification_url,
            step="Load email verification stage",
            headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        if page.status_code >= 400:
            raise _response_error(page, "Load email verification stage")
        sent_at = time.time() - 5
        sent = self._request(
            "GET",
            SEND_EMAIL_OTP_URL,
            step="Send email verification code",
            headers={
                "accept": "application/json, text/plain, */*",
                "referer": verification_url,
                "oai-device-id": self.device_id,
                **generate_datadog_trace_headers(),
            },
        )
        if sent.status_code != 200:
            raise _response_error(sent, "Send email verification code")
        self.log("[邮箱] 协议模式已请求发送 OpenAI 邮箱验证码")
        code = self._wait_for_email_code(sent_at)
        sentinel = self._sentinel_header("email_otp_validate")
        validated = self._request(
            "POST",
            VALIDATE_EMAIL_OTP_URL,
            step="Validate email verification code",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": f"{AUTH_BASE_URL}/email-verification",
                "oai-device-id": self.device_id,
                "openai-sentinel-token": sentinel,
                **generate_datadog_trace_headers(),
            },
            data=json.dumps({"code": code}, separators=(",", ":")),
        )
        if validated.status_code != 200:
            raise _response_error(validated, "Validate email verification code")
        self._emit("email_verified")
        return _json_response(validated, "Validate email verification code")

    def _create_account(self) -> dict[str, Any]:
        name = f"{random.choice(['Mia', 'Ella', 'Luna', 'Noah', 'Leo', 'Mason'])} {random.choice(['Adams', 'Clark', 'Smith', 'Walker', 'Young'])}"
        age = random.randint(25, 34)
        now = datetime.now(timezone.utc)
        birthdate = f"{now.year - age:04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        try:
            self._request(
                "GET",
                f"{AUTH_BASE_URL}/api/accounts/client_auth_session_dump",
                step="Advance authorization state",
                headers={"accept": "application/json", "referer": f"{AUTH_BASE_URL}/email-verification"},
            )
        except ProtocolRegistrationError as exc:
            self.log(f"[认证] 协议状态推进请求未成功，继续创建账户：{exc}")
        sentinel = self._sentinel_header("oauth_create_account")
        response = self._request(
            "POST",
            CREATE_ACCOUNT_URL,
            step="Create ChatGPT account",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": f"{AUTH_BASE_URL}/about-you",
                "oai-device-id": self.device_id,
                "openai-sentinel-token": sentinel,
                **generate_datadog_trace_headers(),
            },
            data=json.dumps({"name": name, "birthdate": birthdate}, separators=(",", ":")),
        )
        if response.status_code != 200:
            raise _response_error(response, "Create ChatGPT account")
        self.auth_action = "register"
        self.log(f"[认证] 协议模式已提交基础资料：{name} / {birthdate}")
        return _json_response(response, "Create ChatGPT account")

    def _finish_session(self, continue_url: str) -> dict[str, Any]:
        target = str(continue_url or self.auth_url or "").strip()
        if target:
            response = self._request(
                "GET",
                target,
                step="Complete OpenAI callback",
                allow_redirects=True,
                headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            )
            if response.status_code >= 400:
                raise _response_error(response, "Complete OpenAI callback")
        session_response = self._request(
            "GET",
            f"{CHATGPT_BASE_URL}/api/auth/session",
            step="Read ChatGPT session",
            headers={"accept": "application/json", "referer": f"{CHATGPT_BASE_URL}/"},
        )
        if session_response.status_code != 200:
            raise _response_error(session_response, "Read ChatGPT session")
        session_json = _json_response(session_response, "Read ChatGPT session")
        access_token = str(session_json.get("accessToken") or session_json.get("access_token") or "")
        if not access_token:
            raise ProtocolRegistrationError("ChatGPT session did not return accessToken")
        account_payload = session_json.get("account") if isinstance(session_json.get("account"), dict) else {}
        plan_type = str(account_payload.get("planType") or account_payload.get("plan_type") or "free").lower()
        session_token = self._cookie("__Secure-next-auth.session-token")
        account_id = self._cookie("_account") or str(account_payload.get("id") or "")
        cookies = []
        try:
            cookies = [
                {"name": item.name, "value": item.value, "domain": item.domain, "path": item.path}
                for item in self.session.cookies.jar
            ]
        except Exception:
            pass
        self._emit("auth_completed")
        result = {
            "access_token": access_token,
            "refresh_token": "",
            "id_token": access_token,
            "session_token": session_token,
            "session_json": session_json,
            "storage_state_json": {"cookies": cookies, "origins": []},
            "account_id": account_id,
            "plan_type": plan_type,
            "auth_action": self.auth_action if self.auth_action != "unknown" else "login",
            "execution_mode": "protocol",
        }
        self._emit("registered", result)
        return result

    def run(self) -> dict[str, Any]:
        self.log(f"[认证] 开始纯协议注册或登录：{self.account.email}")
        self.log("[认证] 协议模式不会启动 Chromium、Camoufox 或其他浏览器")
        try:
            self._check_cancelled()
            self.reader = HotmailReader(self.account, self.log, self.proxy_url)
            self.reader.connect()
            self.session = self.session or self._new_session()
            self._emit("protocol_started")
            self._start_next_auth()
            state = self._authorize_email()
            page_type = str((state.get("page") or {}).get("type") or "")
            continue_url = str(state.get("continue_url") or "")
            self.log(f"[认证] 协议认证状态：{page_type or 'unknown'}")

            if page_type in {"password", "create_account_password"}:
                state = self._submit_password()
                page_type = str((state.get("page") or {}).get("type") or page_type)
                continue_url = str(state.get("continue_url") or continue_url)
            elif page_type in {"login_password"}:
                self.auth_action = "login"
                # Prefer the email OTP endpoint for existing accounts. This
                # avoids treating the Outlook mailbox password as a GPT password.
            elif page_type in {"email_otp_verification", "email_otp_send"}:
                self.auth_action = "login" if self.existing_account else self.auth_action

            state = self._verify_email(continue_url)
            page_type = str((state.get("page") or {}).get("type") or "")
            continue_url = str(state.get("continue_url") or continue_url)
            if page_type in {"about_you", "create_account", "name_and_birthdate"} or self.auth_action == "register":
                state = self._create_account()
                continue_url = str(state.get("continue_url") or continue_url)
            else:
                self.auth_action = "login"
            result = self._finish_session(continue_url)
            self.log("[认证] 纯协议注册/登录完成，已读取 ChatGPT Session")
            return result
        finally:
            if self.reader:
                self.reader.close()
            if self.session:
                try:
                    self.session.close()
                except Exception:
                    pass


def login_or_register_protocol(
    account: MailAccount,
    proxy_url: str = "",
    log: Callable[[str], None] | None = None,
    *,
    existing_account: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return ProtocolRegistrationFlow(
        account,
        proxy_url,
        log,
        existing_account=existing_account,
        should_cancel=should_cancel,
        on_progress=on_progress,
    ).run()
