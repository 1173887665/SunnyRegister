from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import replace
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests

from .db import SunnyDB
from .domain_mail_cleanup import cleanup_failed_mailbox, retain_failed_mailbox
from .mailbox import DomainMailReader, MailAccount, _infer_rebind_mailbox_kind, account_from_row, create_mailbox_reader
from .openai_auth import LoginSecretAuthenticationError, login_or_register
from .protocol_auth import ProtocolChallengeRequired, ProtocolRegistrationFlow

CHATGPT_ORIGIN = "https://chatgpt.com"
ELIGIBILITY_PATH = "/backend-api/accounts/change_email/eligibility"
BEGIN_PATH = "/backend-api/accounts/change_email/begin"
VERIFY_PATH = "/backend-api/accounts/change_email/verify"
# Keep the account API headers aligned with the current ChatGPT web client. A
# stale build can still return HTTP 200 while not starting the email delivery
# workflow, which leaves the mailbox listener waiting until it times out.
CLIENT_VERSION = "prod-180ca8b8699a733aef330b7026892aee9bf85fbe"
CLIENT_BUILD = "9758774"
# CloudMail pickup is near real time. If no message arrives quickly, resend the
# accepted request twice instead of spending several minutes polling an empty
# mailbox: 20s + resend + 45s + resend + 45s.
REBIND_OTP_FIRST_WAIT_SECONDS = 20
REBIND_OTP_SECOND_WAIT_SECONDS = 45
REBIND_OTP_FINAL_WAIT_SECONDS = 45
_DOMAIN_ROTATION = itertools.count()


class RebindError(RuntimeError):
    pass


def _is_retryable_rebind_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in ("timeout", "timed out", "tls", "connection", "curl", "temporarily", "reset", "wrong_version_number"))


def _begin_with_retry(client: "ChangeEmailClient", email: str, log: Callable[[str], None], *, attempts: int = 2) -> dict[str, Any]:
    for attempt in range(1, max(1, attempts) + 1):
        try:
            result = client.begin(email)
            if isinstance(result, dict):
                success = result.get("success")
                if success is False or str(success).strip().lower() in {"false", "0", "no"}:
                    detail = next(
                        (
                            str(result.get(key) or "").strip()
                            for key in ("message", "error", "detail", "code")
                            if str(result.get(key) or "").strip()
                        ),
                        "上游未接受请求",
                    )
                    raise RebindError(f"换绑验证码请求未被接受：{detail[:220]}")
            return result
        except RebindError as exc:
            if attempt >= attempts or not _is_retryable_rebind_error(exc):
                raise
            delay = min(3, attempt)
            log(f"[{email}] 换绑验证码请求遇到瞬时网络错误，将在 {delay} 秒后重试（{attempt + 1}/{attempts}）：{str(exc)[:220]}")
            time.sleep(delay)
    raise RebindError("换绑验证码请求重试失败")


def _cookie_header(session: Any) -> str:
    try:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in session.cookies.jar)
    except Exception:
        try:
            return "; ".join(f"{key}={value}" for key, value in session.cookies.get_dict().items())
        except Exception:
            return ""


def _pickup_token_hash(credential: str) -> str:
    parsed = urlsplit(str(credential or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return ""
    token = parse_qs(parsed.query).get("token", [""])[0]
    return hashlib.sha256(token.encode("utf-8")).hexdigest() if token else ""


def _redact_pickup_credential(credential: str) -> str:
    """Keep pickup diagnostics useful without exposing the one-time token."""
    parsed = urlsplit(str(credential or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "<redacted>"
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "token" in query:
        query["token"] = ["<redacted>"]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query if "token" not in query else urlencode(query, doseq=True), ""))


def _resolve_rebind_mailbox_kind(
    api: str,
    email: str,
    mailbox_type: str = "",
    mailbox_channel: str = "",
) -> tuple[str, str]:
    """Resolve an imported target without losing an explicit provider choice.

    A pickup URL carrying both ``email`` and ``token`` is an unambiguous
    SunnyRegister domain credential, including rows created by older builds
    that were incorrectly labelled as Apple URL API. For all other values,
    keep a supplied type/channel and only infer when the type is absent.
    """
    explicit_type = str(mailbox_type or "").strip().lower()
    explicit_channel = str(mailbox_channel or "").strip().lower()
    detected = _infer_rebind_mailbox_kind(api, email)
    if detected == ("domain", "domain_api"):
        return detected
    if detected == ("apple", "url_api") and explicit_type in {"", "domain"} and explicit_channel in {"", "domain_api"}:
        return detected
    if explicit_type:
        return explicit_type, explicit_channel or (
            "outlook" if explicit_type == "microsoft" else
            "domain_api" if explicit_type == "domain" else
            "url_api" if explicit_type == "apple" else
            "remail_api"
        )
    if detected:
        return detected
    return "domain", "domain_api"


class ChangeEmailClient:
    def __init__(self, flow: ProtocolRegistrationFlow, account_id: str = "", log: Callable[[str], None] | None = None):
        self.flow = flow
        self.session = flow.session
        self.account_id = str(account_id or "").strip()
        self.log = log or (lambda _message: None)
        if self.session is None or not flow.device_id or not self._access_token:
            raise RebindError("旧账号登录态不完整，缺少设备 ID 或 Access Token")
        self.session_id = str(uuid.uuid4())
        self.client_observation = "v1.r.p." + secrets.token_urlsafe(12)
        self.last_begin_accepted = False

    @property
    def _access_token(self) -> str:
        return str(getattr(self.flow, "_last_access_token", "") or "")

    def set_access_token(self, token: str) -> None:
        self.flow._last_access_token = str(token or "")

    def _headers(self, path: str, json_body: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": f"{CHATGPT_ORIGIN}/",
            "oai-device-id": str(self.flow.device_id),
            "oai-session-id": self.session_id,
            "oai-client-version": CLIENT_VERSION,
            "oai-client-build-number": CLIENT_BUILD,
            "oai-language": "zh-CN",
            "x-oai-is-client-observation": self.client_observation,
            "x-openai-target-path": path,
            "x-openai-target-route": path,
        }
        if self.account_id and path != ELIGIBILITY_PATH:
            headers["chatgpt-account-id"] = self.account_id
        cookie = _cookie_header(self.session)
        if cookie:
            headers["Cookie"] = cookie
        if json_body:
            headers.update({"Content-Type": "application/json", "Origin": CHATGPT_ORIGIN})
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = self.session.request(method, f"{CHATGPT_ORIGIN}{path}", headers=self._headers(path, kwargs.get("json") is not None), timeout=30, **kwargs)
        except Exception as exc:
            self.log(f"[换绑接口] {method} {path} 网络请求失败（耗时 {time.monotonic() - started:.1f}s）：{str(exc)[:300]}")
            raise RebindError(f"换绑接口网络请求失败：{method} {path}: {exc}") from exc
        body = str(getattr(response, "text", "") or "")[:1000]
        request_id = str(getattr(response, "headers", {}).get("x-request-id") or "").strip()
        request_suffix = f"，request_id={request_id}" if request_id else ""
        self.log(f"[换绑接口] {method} {path} -> HTTP {response.status_code}（耗时 {time.monotonic() - started:.1f}s{request_suffix}）")
        if response.status_code < 200 or response.status_code >= 300:
            if "email_change_account_too_new" in body.lower():
                raise RebindError(
                    "上游仍判定账号处于注册后的邮箱换绑冷却期（email_change_account_too_new）；"
                    "当前账号暂不能换绑，请等待上游冷却结束后重试"
                )
            if response.status_code in {401, 403} or "reauth" in body.lower() or "recent" in body.lower():
                raise RebindError(f"换绑接口需要重新认证：HTTP {response.status_code} {body}")
            raise RebindError(f"换绑接口 {path} 失败：HTTP {response.status_code} {body}")
        try:
            value = response.json()
            result = value if isinstance(value, dict) else {"value": value}
            if path == BEGIN_PATH:
                # Do not log email, tokens, or response bodies; the field list
                # and a few non-sensitive status values are enough to diagnose
                # an accepted request that did not trigger mailbox delivery.
                keys = ",".join(sorted(str(key) for key in result.keys())) or "无"
                status = {key: result[key] for key in ("success", "status", "state", "message", "error", "code") if key in result}
                self.log(f"[换绑接口] begin 响应摘要：字段={keys}，状态={json.dumps(status, ensure_ascii=False, separators=(',', ':')) if status else '无'}")
            return result
        except Exception:
            if path == BEGIN_PATH:
                self.log("[换绑接口] begin 响应摘要：非 JSON 或响应体为空")
            return {"ok": True}

    def eligibility(self) -> dict[str, Any]:
        result = self._request("GET", ELIGIBILITY_PATH)
        if result.get("eligible") is not True:
            raise RebindError(f"当前账户不允许邮箱换绑：{result}")
        return result

    def begin(self, email: str) -> dict[str, Any]:
        result = self._request("POST", BEGIN_PATH, json={"email": email})
        success = result.get("success") if isinstance(result, dict) else None
        self.last_begin_accepted = success is not False and str(success).strip().lower() not in {"false", "0", "no"}
        return result

    def verify(self, email: str, code: str) -> dict[str, Any]:
        return self._request("POST", VERIFY_PATH, json={"email": email, "code": code})


def _domain_mailbox(db: SunnyDB, log: Callable[[str], None]) -> tuple[str, str, str]:
    cfg = db.get_config("domain_mailbox")
    if cfg.get("enabled_for_rebinding") is not True:
        raise RebindError("自建域名邮箱未启用邮箱换绑")
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    token = str(cfg.get("auth_token") or "").strip()
    site_password = str(cfg.get("site_password") or "").strip()
    raw_domains = cfg.get("domains")
    if isinstance(raw_domains, (list, tuple)):
        domain_values = [str(value or "") for value in raw_domains]
    else:
        domain_values = re.split(r"[,;\r\n]+", str(raw_domains or ""))
    domain_values = [value.strip().lstrip("@").lower() for value in domain_values if value.strip()]
    if not domain_values and str(cfg.get("domain") or "").strip():
        domain_values = [str(cfg.get("domain") or "").strip().lstrip("@").lower()]
    domains = list(dict.fromkeys(domain_values))
    pickup_base = str(cfg.get("pickup_base_url") or os.getenv("SUNNY_PUBLIC_ORIGIN") or "").strip().rstrip("/")
    pickup_parts = urlsplit(pickup_base)
    if not base or not token or not domains or any("@" in domain or "." not in domain or any(char.isspace() for char in domain) for domain in domains):
        raise RebindError("自建域名邮箱配置不完整，请填写 CloudMail API、PUBLIC_API_TOKEN 和域名")
    if pickup_parts.scheme not in {"http", "https"} or not pickup_parts.netloc:
        raise RebindError("请先配置可公网访问的 SunnyRegister 取件 API 地址")
    length = max(6, min(32, int(cfg.get("random_local_length") or 12)))
    domain = domains[next(_DOMAIN_ROTATION) % len(domains)]
    proxies = None
    for _ in range(8):
        local = re.sub(r"[^a-z0-9]", "", secrets.token_urlsafe(length + 4).lower())[:length]
        email = f"{local}@{domain}"
        try:
            headers = {"Accept": "application/json", "Authorization": token, "X-Auth-Token": token, "User-Agent": "SunnyRegister/1.0"}
            if site_password:
                headers["x-custom-auth"] = site_password
            response = requests.post(
                base + "/api/public/addUser",
                json={"list": [{"email": email, "password": secrets.token_urlsafe(18)}]},
                headers=headers,
                timeout=30,
                proxies=proxies,
            )
            payload = {}
            try:
                payload = response.json()
            except Exception:
                pass
            provider_code = str(payload.get("code") or "") if isinstance(payload, dict) else ""
            if response.ok and provider_code not in {"", "0", "200"}:
                last = f"provider code {provider_code}: {str(payload.get('message') or payload.get('error') or '')[:180]}"
            elif response.ok:
                pickup_token = "dmsk_" + secrets.token_urlsafe(32)
                credential = pickup_base + "/api/sunny/domain-mail/pickup?" + urlencode({"email": email, "token": pickup_token})
                token_hash = hashlib.sha256(pickup_token.encode("utf-8")).hexdigest()
                log(
                    f"[{email}] 已从自建域名邮箱池生成换绑邮箱，"
                    f"取件地址={_redact_pickup_credential(credential)}"
                )
                return email, credential, token_hash
        except requests.RequestException as exc:
            last = str(exc)
        else:
            last = f"HTTP {response.status_code}: {str(response.text or '')[:180]}"
    raise RebindError(f"生成自建域名邮箱失败：{last}")


def _login_flow(account: MailAccount, proxy: str, log: Callable[[str], None], *, keep_session: bool, should_cancel: Callable[[], bool] | None = None) -> tuple[ProtocolRegistrationFlow, dict[str, Any]]:
    if not account.has_login_secret:
        log("[认证] 未检测到完整 LS，直接使用邮箱验证码建立全新认证事务")
        return _browser_mailbox_fallback(
            account,
            proxy,
            log,
            keep_session=keep_session,
            should_cancel=should_cancel,
        )
    flow = ProtocolRegistrationFlow(
        account,
        proxy,
        log,
        existing_account=True,
        should_cancel=should_cancel,
        challenge_strategy="sentinel_protocol",
        keep_session=keep_session,
        skip_mailbox=True,
    )
    try:
        result = flow.run()
    except ProtocolChallengeRequired as exc:
        if should_cancel and should_cancel():
            raise
        log(
            "[认证] Sentinel 协议运行时遇到浏览器挑战，自动切换 Camoufox 后台无头登录；"
            "仍优先使用完整 LS，LS 失败时再使用邮箱凭证"
        )
        try:
            result = login_or_register(
                account,
                proxy,
                True,
                log,
                existing_account=True,
                require_refresh_token=False,
                should_cancel=should_cancel,
                execution_mode="protocol_headless_fallback",
            )
        except Exception as browser_exc:
            if not _should_use_mailbox_browser_fallback(browser_exc):
                raise
            # A browser page can remain on /log-in/password without exposing
            # an email-code switch. Retry with the protocol OTP state machine
            # so this case does not fail before the mailbox is consulted.
            try:
                if flow.session:
                    flow.session.close()
            except Exception:
                pass
            return _browser_mailbox_fallback(
                account,
                proxy,
                log,
                keep_session=keep_session,
                should_cancel=should_cancel,
            )
        _hydrate_protocol_flow_from_browser(flow, result)
        result["requested_execution_mode"] = "protocol"
        result["execution_mode"] = "protocol_headless_fallback"
        result["protocol_fallback"] = "headless"
        protocol_traffic = getattr(exc, "traffic", None)
        if isinstance(protocol_traffic, dict):
            result["protocol_traffic"] = protocol_traffic
        log("[认证] 邮箱换绑的后台无头浏览器登录已完成，继续执行换绑接口")
    except Exception as exc:
        if should_cancel and should_cancel():
            raise
        if not _should_use_mailbox_browser_fallback(exc):
            raise
        log(
            "[认证] 协议登录的授权事务连续失效，正在使用邮箱凭证建立全新认证事务："
            f"{str(exc)[:220]}"
        )
        try:
            if flow.reader:
                flow.reader.close()
            if flow.session:
                flow.session.close()
        except Exception:
            pass
        return _browser_mailbox_fallback(
            account,
            proxy,
            log,
            keep_session=keep_session,
            should_cancel=should_cancel,
        )
    flow._last_access_token = str(result.get("access_token") or "")
    return flow, result


def _hydrate_protocol_flow_from_browser(flow: ProtocolRegistrationFlow, result: dict[str, Any]) -> None:
    """Move a completed browser login into the HTTP session used by rebind APIs."""
    access_token = str(result.get("access_token") or "").strip()
    state = result.get("storage_state_json")
    cookies = state.get("cookies") if isinstance(state, dict) else None
    if not access_token or not isinstance(cookies, list) or not cookies:
        raise RebindError("无头浏览器登录结果不完整，缺少 Access Token 或认证 Cookie")
    if flow.session is None:
        flow.session = flow._new_session()
    try:
        flow.session.cookies.clear()
    except Exception:
        pass
    device_id = ""
    account_id = ""
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        if not name:
            continue
        cookie_options: dict[str, Any] = {}
        domain = str(item.get("domain") or "").strip()
        path = str(item.get("path") or "/").strip() or "/"
        if domain:
            cookie_options["domain"] = domain
        cookie_options["path"] = path
        cookie_options["secure"] = bool(item.get("secure"))
        flow.session.cookies.set(name, value, **cookie_options)
        if name == "oai-did" and value:
            device_id = value
        elif name == "_account" and value:
            account_id = value
    session_json = result.get("session_json")
    session_account = session_json.get("account") if isinstance(session_json, dict) else None
    if isinstance(session_account, dict):
        account_id = str(session_account.get("id") or account_id).strip()
    if account_id and not str(result.get("account_id") or "").strip():
        result["account_id"] = account_id
    flow.device_id = device_id or str(uuid.uuid4())
    flow._last_access_token = access_token


def _should_use_mailbox_browser_fallback(error: Exception) -> bool:
    """Identify an LS/browser failure that can safely retry with mailbox OTP."""
    message = str(error or "").lower()
    if any(
        marker in message
        for marker in (
            "account_deactivated",
            "account disabled",
            "account banned",
            "account suspended",
            "账号已封禁",
            "账户已封禁",
            "账户已停用",
        )
    ):
        return False
    if isinstance(error, LoginSecretAuthenticationError):
        return True
    return any(
        marker in message
        for marker in (
            "未提供邮箱验证码切换入口",
            "邮箱验证码切换入口",
            "ls login",
            "密码登录未完成",
            "密码提交后认证页面未继续",
            "2fa 提交后认证页面未继续",
            "interactive anti-bot challenge",
            "upstream html challenge",
            "requires an interactive",
            "invalid_auth_step",
            "invalid_state",
            "session is no longer valid",
            "session has expired",
            "session expired",
            "セッションは無効",
            "セッションの有効期限",
            "invalid authorization step",
            "认证步骤不匹配",
        )
    )


def _browser_mailbox_fallback(
    account: MailAccount,
    proxy: str,
    log: Callable[[str], None],
    *,
    keep_session: bool,
    should_cancel: Callable[[], bool] | None,
) -> tuple[ProtocolRegistrationFlow, dict[str, Any]]:
    """Retry an LS failure with a fresh mailbox-only auth transaction.

    Clearing only the ChatGPT password/TOTP fields forces the login state
    machine to use the mailbox credentials while preserving the account
    identity. Start with a fresh protocol transaction because the current
    password page may not expose an email-code switch. Camoufox remains the
    fallback for Turnstile and device challenges.
    """
    mailbox_account = replace(account, chatgpt_password="", totp_secret="")
    if account.has_login_secret:
        log("[认证] LS 登录未完成，改用邮箱验证码登录重试；保留当前账户邮箱凭证")
    else:
        log("[认证] 使用邮箱验证码登录；保留当前账户邮箱凭证")
    flow = ProtocolRegistrationFlow(
        mailbox_account,
        proxy,
        log,
        existing_account=True,
        should_cancel=should_cancel,
        challenge_strategy="sentinel_protocol",
        keep_session=keep_session,
        skip_mailbox=False,
    )
    try:
        result = flow.run()
    except Exception as protocol_exc:
        if should_cancel and should_cancel():
            raise
        if not _should_use_mailbox_browser_fallback(protocol_exc) and any(
            marker in str(protocol_exc or "").lower()
            for marker in (
                "account_deactivated",
                "account disabled",
                "account banned",
                "account suspended",
                "账号已封禁",
                "账户已封禁",
                "账户已停用",
            )
        ):
            raise
        log(
            "[认证] 邮箱凭证协议登录仍需浏览器挑战，正在切换浏览器全新会话："
            f"{str(protocol_exc)[:220]}"
        )
        try:
            reader = getattr(flow, "reader", None)
            if reader:
                reader.close()
            session = getattr(flow, "session", None)
            if session:
                session.close()
        except Exception:
            pass
        flow.session = None
    else:
        flow._last_access_token = str(result.get("access_token") or "")
        result["requested_execution_mode"] = "protocol"
        result["execution_mode"] = "protocol"
        result["protocol_fallback"] = "mailbox_protocol"
        log("[认证] 全新邮箱凭证协议登录完成，继续执行邮箱换绑")
        return flow, result
    result = None
    for attempt in range(2):
        try:
            result = login_or_register(
                mailbox_account,
                proxy,
                True,
                log,
                existing_account=True,
                require_refresh_token=False,
                should_cancel=should_cancel,
                execution_mode="protocol_headless_fallback",
            )
            break
        except Exception as exc:
            message = str(exc or "").lower()
            retryable = any(marker in message for marker in (
                "challenge", "turnstile", "captcha", "cloudflare", "timed out", "timeout",
                "connection reset", "connection refused", "curl: (", "tls",
            ))
            if attempt != 0 or not retryable:
                raise
            log("[认证] 邮箱验证码浏览器登录遇到临时挑战或网络错误，正在建立全新会话重试一次")
    if result is None:
        raise RebindError("邮箱验证码浏览器登录未返回结果")
    _hydrate_protocol_flow_from_browser(flow, result)
    result["execution_mode"] = "protocol_headless_fallback"
    result["protocol_fallback"] = "mailbox_browser"
    log("[认证] 浏览器邮箱验证码登录完成，继续执行邮箱换绑")
    return flow, result


def _phone_session_flow(
    db: SunnyDB,
    account: MailAccount,
    account_row: dict[str, Any],
    proxy: str,
    log: Callable[[str], None],
) -> tuple[ProtocolRegistrationFlow, dict[str, Any]]:
    """Restore a phone-only account from its persisted ChatGPT session.

    A phone registration has no mailbox credentials, so rebind must not try
    to authenticate through an email reader. The session captured immediately
    after registration already contains the AT and cookies required by the
    change-email endpoints.
    """
    account_id = int(account_row.get("id") or 0)
    session_row = db.fetch_session_by_account_id(account_id) if account_id > 0 else None
    if not session_row:
        raise RebindError("手机号账户缺少已保存的登录会话，请先完成注册或刷新会话")
    access_token = str(session_row.get("access_token") or "").strip()
    if not access_token:
        raise RebindError("手机号账户会话缺少 Access Token，请先刷新会话")
    def parse_json(value: Any, default: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        try:
            parsed = json.loads(str(value or ""))
            return parsed if isinstance(parsed, type(default)) else default
        except (TypeError, ValueError, json.JSONDecodeError):
            return default
    session_json = parse_json(session_row.get("session_json"), {})
    storage_state = parse_json(session_row.get("storage_state_json"), {})
    if not isinstance(storage_state, dict) or not isinstance(storage_state.get("cookies"), list):
        raise RebindError("手机号账户会话缺少认证 Cookie，请先刷新会话")
    flow = ProtocolRegistrationFlow(
        account,
        proxy,
        log,
        existing_account=True,
        should_cancel=db.cancel_requested,
        challenge_strategy="sentinel_protocol",
        keep_session=True,
        skip_mailbox=True,
    )
    flow.session = flow._new_session()
    result = {
        "access_token": access_token,
        "refresh_token": str(session_row.get("refresh_token") or "").strip(),
        "id_token": str(session_row.get("id_token") or access_token),
        "session_json": session_json,
        "storage_state_json": storage_state,
        "account_id": str((session_json.get("account") or {}).get("id") or "") if isinstance(session_json, dict) else "",
    }
    _hydrate_protocol_flow_from_browser(flow, result)
    flow._last_access_token = access_token
    return flow, result


def _persist_login_result(db: SunnyDB, identity_email: str, mailbox: dict[str, Any], result: dict[str, Any], log: Callable[[str], None]) -> None:
    persist = getattr(db, "persist_authenticated_session", None)
    if not callable(persist):
        return
    persist(
        identity_email,
        int(mailbox.get("id") or 0),
        result,
        str(mailbox.get("raw") or ""),
    )
    log(f"[{identity_email}] 登录成功后已立即同步最新 Access Token")


def _wait_for_rebind_code(reader: DomainMailReader, client: ChangeEmailClient, email: str, min_timestamp: float, log: Callable[[str], None]) -> str:
    """Use two bounded resends when an accepted request is not delivered."""
    try:
        return reader.wait_for_code(min_timestamp, timeout=REBIND_OTP_FIRST_WAIT_SECONDS)
    except TimeoutError:
        log(f"[{email}] 首次换绑验证码请求已接受但 {REBIND_OTP_FIRST_WAIT_SECONDS} 秒内未收到邮件，进行第 1 次重发")
        _begin_with_retry(client, email, log)
        log(f"[{email}] 第 1 次重发已接受，继续等待邮箱投递")
    try:
        return reader.wait_for_code(min_timestamp, timeout=REBIND_OTP_SECOND_WAIT_SECONDS)
    except TimeoutError:
        log(f"[{email}] 第 1 次重发后 {REBIND_OTP_SECOND_WAIT_SECONDS} 秒内仍未收到邮件，进行第 2 次重发")
        _begin_with_retry(client, email, log)
        log(f"[{email}] 第 2 次重发已接受，进行最后一次邮箱等待")
        try:
            return reader.wait_for_code(min_timestamp, timeout=REBIND_OTP_FINAL_WAIT_SECONDS)
        except TimeoutError as exc:
            raw_count = int(getattr(reader, "last_raw_count", 0) or 0)
            if getattr(client, "last_begin_accepted", False) and raw_count == 0:
                status = int(getattr(reader, "last_status", 0) or 0)
                last_error = str(getattr(reader, "last_error", "") or "").strip()
                if status == 200 and not last_error:
                    raise TimeoutError(
                        "上游未实际投递换绑验证码：begin 已返回 HTTP 200/success=true，"
                        "CloudMail 查询也持续返回 HTTP 200，但收件箱没有新邮件；"
                        "当前失败点在上游发信/验证码任务，重试时会重新建立认证会话并轮换代理"
                    ) from exc
                raise TimeoutError(
                    "上游未实际投递换绑验证码：begin 已返回 HTTP 200/success=true，"
                    f"但 CloudMail 收件箱持续为空（HTTP {status or '未知'}{('，' + last_error[:120]) if last_error else ''}）；"
                    "请检查上游发信投递状态"
                ) from exc
            raise


def _requires_fresh_email_auth(error: Exception) -> bool:
    """Return true for a rejected LS step whose OAuth transaction is stale."""
    message = str(error or "").lower()
    return any(
        marker in message
        for marker in (
            "invalid_auth_step",
            "invalid authorization step",
            "认证步骤不匹配",
            "密码提交后认证页面未继续",
            "2fa 提交后认证页面未继续",
        )
    )


def _handle_failed_domain_mailbox(
    db: SunnyDB,
    old_email: str,
    new_email: str,
    new_api: str,
    pickup_token_hash: str,
    error: Exception,
    log: Callable[[str], None],
    *,
    mailbox_type: str = "domain",
    mailbox_channel: str = "domain_api",
) -> None:
    try:
        detected_kind = _infer_rebind_mailbox_kind(new_api, new_email)
    except ValueError:
        detected_kind = None
    if detected_kind:
        mailbox_type, mailbox_channel = detected_kind
    mailbox_type = str(mailbox_type or "domain").strip().lower() or "domain"
    mailbox_channel = str(mailbox_channel or "").strip().lower() or (
        "url_api" if mailbox_type == "apple" else "remail_api" if mailbox_type == "remail" else "domain_api"
    )
    cfg = db.get_config("domain_mailbox")
    if retain_failed_mailbox(cfg):
        try:
            db.persist_rebind_failure(
                old_email, new_email, new_api, pickup_token_hash, str(error), mailbox_type, mailbox_channel
            )
            log(f"[{old_email}] 换绑失败邮箱已保存到邮箱池：{new_email}")
        except Exception as persist_exc:
            log(f"[{old_email}] 保存失败邮箱记录失败：{persist_exc}")
        return
    if mailbox_type != "domain":
        try:
            removed = db.delete_failed_domain_mailbox(new_email, pickup_token_hash)
            message = "已清理本地失败邮箱记录" if removed else "本地未找到匹配的失败邮箱记录"
            log(f"[{old_email}] {message}：{new_email}")
        except Exception as cleanup_exc:
            log(f"[{old_email}] 失败邮箱清理未完全完成：{cleanup_exc}")
        return
    try:
        cleanup_failed_mailbox(db, cfg, new_email, pickup_token_hash, log)
        log(f"[{old_email}] 换绑失败邮箱已按配置清理：{new_email}")
    except Exception as cleanup_exc:
        log(f"[{old_email}] 失败邮箱清理未完全完成：{cleanup_exc}")


def _login_rebound_account(
    account: MailAccount,
    proxy: str,
    log: Callable[[str], None],
    *,
    should_cancel: Callable[[], bool] | None,
) -> tuple[ProtocolRegistrationFlow, dict[str, Any]]:
    """Login the verified replacement address, retrying one fresh auth session."""
    for attempt in range(2):
        try:
            return _login_flow(account, proxy, log, keep_session=False, should_cancel=should_cancel)
        except Exception as exc:
            retryable = _is_retryable_rebind_error(exc) or _should_use_mailbox_browser_fallback(exc)
            if attempt > 0 or not retryable:
                raise
            log(f"[{account.email}] 换绑后新邮箱登录未完成，建立全新认证会话重试一次：{str(exc)[:260]}")
    raise RebindError("换绑后新邮箱登录未返回结果")


def _rebind_target_account(email: str, api: str, mailbox_type: str, mailbox_channel: str, source: MailAccount) -> MailAccount:
    """Build a mailbox reader account from an imported target credential."""
    normalized_type = str(mailbox_type or "domain").strip().lower()
    normalized_channel = str(mailbox_channel or "").strip().lower()
    raw = f"{email}----{api}"
    if normalized_type == "microsoft":
        parts = [part.strip() for part in str(api or "").split("----")]
        if len(parts) != 3 or not all(parts):
            raise RebindError("微软换绑邮箱凭证必须为 password----client_id----refresh_token")
        return MailAccount(
            email=email,
            password=parts[0],
            client_id=parts[1],
            refresh_token=parts[2],
            raw=raw,
            mailbox_type="microsoft",
            mailbox_channel="outlook",
            chatgpt_password=source.chatgpt_password,
            totp_secret=source.totp_secret,
        )
    return MailAccount(
        email=email,
        password="",
        client_id="",
        refresh_token="",
        raw=raw,
        mailbox_type=normalized_type,
        mailbox_channel=normalized_channel or ("domain_api" if normalized_type == "domain" else "url_api" if normalized_type == "apple" else "remail_api"),
        access_key=api,
        chatgpt_password=source.chatgpt_password,
        totp_secret=source.totp_secret,
    )


def rebind_one(db: SunnyDB, account_row: dict[str, Any], proxy: str, log: Callable[[str], None]) -> dict[str, Any]:
    old_email = str(account_row.get("email") or "").strip()
    if not old_email:
        raise RebindError("账户邮箱为空")
    mailbox = db.fetch_mailbox_by_email(old_email)
    phone_only = not mailbox and (
        int(account_row.get("mailbox_id") or 0) <= 0
        and bool(str(account_row.get("phone_number") or "").strip())
    )
    if not mailbox and not phone_only:
        raise RebindError("未找到关联邮箱记录")
    if str(account_row.get("status") or "").strip().lower() in {"banned", "已封禁", "disabled"} or (mailbox and str(mailbox.get("status") or "").strip().lower() in {"banned", "已封禁", "disabled"}):
        return {"email": old_email, "status": "skipped", "reason": "账户已封禁"}
    if phone_only:
        account = MailAccount(
            email=old_email,
            password="",
            client_id="",
            refresh_token="",
            raw=f"{old_email}----phone",
            account_type=str(account_row.get("account_type") or "free"),
            openai_rt=str(account_row.get("openai_rt") or ""),
            mailbox_type="phone",
            mailbox_channel="sms",
        )
    else:
        merged = {**(mailbox or {}), **account_row}
        merged["email"] = old_email
        account = account_from_row(merged)
    new_email = ""
    new_api = ""
    new_api_token_hash = ""
    old_flow = None
    new_flow = None
    phase = "login"
    try:
        verified_email = str(account_row.get("rebind_email") or "").strip()
        verified_api = str(account_row.get("rebind_mailbox_api") or "").strip()
        if verified_email and verified_api and verified_email.lower() != old_email.lower():
            phase = "post_login"
            verified_type, verified_channel = _resolve_rebind_mailbox_kind(
                verified_api,
                verified_email,
                str((mailbox or {}).get("mailbox_type") or ""),
                str((mailbox or {}).get("mailbox_channel") or ""),
            )
            verified_hash = _pickup_token_hash(verified_api)
            resumed_account = replace(
                account,
                email=verified_email,
                raw=f"{verified_email}----{verified_api}",
                mailbox_type=verified_type,
                mailbox_channel=verified_channel,
                access_key=verified_api,
            )
            log(f"[{old_email}] 检测到已验证换绑断点，直接恢复新邮箱登录：{verified_email}")
            new_flow, new_result = _login_rebound_account(
                resumed_account, proxy, log, should_cancel=db.cancel_requested
            )
            if not str(new_result.get("access_token") or "").strip():
                raise RebindError("换绑断点恢复登录未返回新的 Access Token")
            if not str(new_result.get("refresh_token") or "").strip() and account.openai_rt:
                new_result["refresh_token"] = account.openai_rt
            _persist_login_result(db, old_email, mailbox or {}, new_result, log)
            if phone_only:
                db.persist_phone_rebind_verified(int(account_row.get("id") or 0), old_email, verified_email, verified_api, verified_hash, new_result)
            else:
                db.persist_rebind(
                    old_email, verified_email, verified_api, verified_hash, new_result, verified_type, verified_channel
                )
            log(f"[{old_email}] 换绑断点恢复成功：{verified_email}")
            return {"email": old_email, "new_email": verified_email, "status": "success", "resumed": True}

        log(f"[{old_email}] 开始协议换绑")
        if phone_only:
            old_flow, old_result = _phone_session_flow(db, account, account_row, proxy, log)
        else:
            old_flow, old_result = _login_flow(account, proxy, log, keep_session=True, should_cancel=db.cancel_requested)
        _persist_login_result(db, old_email, mailbox or {}, old_result, log)
        client = ChangeEmailClient(old_flow, str(old_result.get("account_id") or ""), log)
        client.set_access_token(str(old_result.get("access_token") or ""))
        phase = "eligibility"
        client.eligibility()
        imported_email = str(account_row.get("_rebind_target_email") or "").strip()
        imported_api = str(account_row.get("_rebind_target_api") or "").strip()
        imported_type = str(account_row.get("_rebind_target_type") or "").strip().lower()
        imported_channel = str(account_row.get("_rebind_target_channel") or "").strip().lower()
        if imported_email and imported_api:
            imported_type, imported_channel = _resolve_rebind_mailbox_kind(
                imported_api, imported_email, imported_type, imported_channel
            )
            new_email, new_api = imported_email, imported_api
            new_api_token_hash = _pickup_token_hash(new_api)
            log(f"[{old_email}] 使用已导入域名邮箱：{new_email}")
        else:
            imported_type = "domain"
            new_email, new_api, new_api_token_hash = _domain_mailbox(db, log)
        # Register the one-time pickup credential before ChatGPT sends the verification mail.
        # The public pickup endpoint validates the token against this database row.
        target_channel = imported_channel or ("outlook" if imported_type == "microsoft" else "domain_api" if imported_type == "domain" else "url_api" if imported_type == "apple" else "remail_api")
        db.persist_rebind_pending(new_email, new_api, new_api_token_hash, imported_type, target_channel)
        reader_account = _rebind_target_account(new_email, new_api, imported_type, target_channel, account)
        reader = create_mailbox_reader(reader_account, log)
        try:
            phase = "mailbox"
            reader.connect()
            issued_after = time.time()
            log(f"[{old_email}] 已建立换绑邮箱取件监听，准备请求 ChatGPT 发送验证码")
            try:
                phase = "begin"
                _begin_with_retry(client, new_email, log)
                log(f"[{old_email}] ChatGPT 换绑验证码请求已接受，等待新邮箱验证码")
            except RebindError as exc:
                if "重新认证" not in str(exc):
                    raise
                previous_flow = old_flow
                phase = "login"
                old_flow, old_result = _login_flow(account, proxy, log, keep_session=True, should_cancel=db.cancel_requested)
                _persist_login_result(db, old_email, mailbox or {}, old_result, log)
                try:
                    if previous_flow and previous_flow.session:
                        previous_flow.session.close()
                except Exception:
                    pass
                client = ChangeEmailClient(old_flow, str(old_result.get("account_id") or ""), log)
                client.set_access_token(str(old_result.get("access_token") or ""))
                phase = "begin"
                _begin_with_retry(client, new_email, log)
                log(f"[{old_email}] 重新认证后已重新提交换绑验证码请求，等待新邮箱验证码")
            phase = "delivery"
            code = _wait_for_rebind_code(reader, client, new_email, issued_after, log)
        finally:
            reader.close()
        phase = "verify"
        client.verify(new_email, code)
        log(f"[{old_email}] 已向 ChatGPT 提交换绑邮箱验证码")
        if phone_only:
            db.persist_phone_rebind_verified(int(account_row.get("id") or 0), old_email, new_email, new_api, new_api_token_hash, old_result)
        else:
            db.persist_rebind_verified(
                old_email, new_email, new_api, new_api_token_hash, imported_type, target_channel
            )
        log(f"[{old_email}] 已保存上游换绑验证断点，后续登录失败可直接恢复")
        new_account = _rebind_target_account(new_email, new_api, imported_type, target_channel, account)
        phase = "post_login"
        new_flow, new_result = _login_rebound_account(
            new_account, proxy, log, should_cancel=db.cancel_requested
        )
        if str(new_result.get("access_token") or "").strip() == "":
            raise RebindError("换绑后重新登录未返回新的 Access Token")
        if not str(new_result.get("refresh_token") or "").strip() and account.openai_rt:
            new_result["refresh_token"] = account.openai_rt
        _persist_login_result(db, old_email, mailbox or {}, new_result, log)
        if phone_only:
            db.persist_phone_rebind_verified(int(account_row.get("id") or 0), old_email, new_email, new_api, new_api_token_hash, new_result)
        else:
            db.persist_rebind(old_email, new_email, new_api, new_api_token_hash, new_result, imported_type, target_channel)
        log(f"[{old_email}] 换绑成功：{new_email}")
        return {"email": old_email, "new_email": new_email, "status": "success"}
    except Exception as exc:
        try:
            if not str(getattr(exc, "rebind_phase", "") or ""):
                exc.rebind_phase = phase
        except Exception:
            pass
        if new_email and new_api:
            _handle_failed_domain_mailbox(
                db,
                old_email,
                new_email,
                new_api,
                new_api_token_hash,
                exc,
                log,
                mailbox_type=imported_type,
                mailbox_channel=target_channel,
            )
        raise
    finally:
        for flow in (old_flow, new_flow):
            if flow is None:
                continue
            try:
                if flow.reader:
                    flow.reader.close()
                if flow.session:
                    flow.session.close()
            except Exception:
                pass
