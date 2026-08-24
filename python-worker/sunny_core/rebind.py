from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import secrets
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import requests

from .db import SunnyDB
from .domain_mail_cleanup import cleanup_failed_mailbox
from .mailbox import DomainMailReader, MailAccount, account_from_row
from .protocol_auth import ProtocolRegistrationFlow

CHATGPT_ORIGIN = "https://chatgpt.com"
ELIGIBILITY_PATH = "/backend-api/accounts/change_email/eligibility"
BEGIN_PATH = "/backend-api/accounts/change_email/begin"
VERIFY_PATH = "/backend-api/accounts/change_email/verify"
# Keep the account API headers aligned with the current ChatGPT web client. A
# stale build can still return HTTP 200 while not starting the email delivery
# workflow, which leaves the mailbox listener waiting until it times out.
CLIENT_VERSION = "prod-180ca8b8699a733aef330b7026892aee9bf85fbe"
CLIENT_BUILD = "9758774"
REBIND_OTP_FIRST_WAIT_SECONDS = 45
REBIND_OTP_RETRY_WAIT_SECONDS = 75
_DOMAIN_ROTATION = itertools.count()


class RebindError(RuntimeError):
    pass


def _is_retryable_rebind_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in ("timeout", "timed out", "tls", "connection", "curl", "temporarily", "reset", "wrong_version_number"))


def _begin_with_retry(client: "ChangeEmailClient", email: str, log: Callable[[str], None], *, attempts: int = 2) -> dict[str, Any]:
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return client.begin(email)
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
        return self._request("POST", BEGIN_PATH, json={"email": email})

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
    if not base or not token or not site_password or not domains or any("@" in domain or "." not in domain or any(char.isspace() for char in domain) for domain in domains):
        raise RebindError("自建域名邮箱配置不完整，请填写 CloudMail API、PUBLIC_API_TOKEN、PASSWORDS 和域名")
    if pickup_parts.scheme not in {"http", "https"} or not pickup_parts.netloc:
        raise RebindError("请先配置可公网访问的 SunnyRegister 取件 API 地址")
    length = max(6, min(32, int(cfg.get("random_local_length") or 12)))
    domain = domains[next(_DOMAIN_ROTATION) % len(domains)]
    proxies = None
    for _ in range(8):
        local = re.sub(r"[^a-z0-9]", "", secrets.token_urlsafe(length + 4).lower())[:length]
        email = f"{local}@{domain}"
        try:
            response = requests.post(
                base + "/api/public/addUser",
                json={"list": [{"email": email, "password": secrets.token_urlsafe(18)}]},
                headers={"Accept": "application/json", "Authorization": token, "X-Auth-Token": token, "x-custom-auth": site_password, "User-Agent": "SunnyRegister/1.0"},
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
                log(f"[{email}] 已从自建域名邮箱池生成换绑邮箱：{email}----{credential}")
                return email, credential, token_hash
        except requests.RequestException as exc:
            last = str(exc)
        else:
            last = f"HTTP {response.status_code}: {str(response.text or '')[:180]}"
    raise RebindError(f"生成自建域名邮箱失败：{last}")


def _login_flow(account: MailAccount, proxy: str, log: Callable[[str], None], *, keep_session: bool, should_cancel: Callable[[], bool] | None = None) -> tuple[ProtocolRegistrationFlow, dict[str, Any]]:
    if not account.has_login_secret:
        raise RebindError("账户缺少 ChatGPT 密码或 2FA，无法执行协议换绑")
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
    result = flow.run()
    flow._last_access_token = str(result.get("access_token") or "")
    return flow, result


def _wait_for_rebind_code(reader: DomainMailReader, client: ChangeEmailClient, email: str, min_timestamp: float, log: Callable[[str], None]) -> str:
    """Mirror the web UI's resend path when the first accepted request is not delivered."""
    try:
        return reader.wait_for_code(min_timestamp, timeout=REBIND_OTP_FIRST_WAIT_SECONDS)
    except TimeoutError:
        log(f"[{email}] 首次换绑验证码请求已接受但 {REBIND_OTP_FIRST_WAIT_SECONDS} 秒内未收到邮件，自动重新请求一次")
        _begin_with_retry(client, email, log)
        log(f"[{email}] 已重新请求换绑验证码，继续等待邮箱投递")
        return reader.wait_for_code(min_timestamp, timeout=REBIND_OTP_RETRY_WAIT_SECONDS)


def rebind_one(db: SunnyDB, account_row: dict[str, Any], proxy: str, log: Callable[[str], None]) -> dict[str, Any]:
    old_email = str(account_row.get("email") or "").strip()
    if not old_email:
        raise RebindError("账户邮箱为空")
    mailbox = db.fetch_mailbox_by_email(old_email)
    if not mailbox:
        raise RebindError("未找到关联邮箱记录")
    if str(account_row.get("status") or "").strip().lower() in {"banned", "已封禁", "disabled"} or str(mailbox.get("status") or "").strip().lower() in {"banned", "已封禁", "disabled"}:
        return {"email": old_email, "status": "skipped", "reason": "账户已封禁"}
    merged = {**mailbox, **account_row}
    merged["email"] = old_email
    account = account_from_row(merged)
    new_email = ""
    new_api = ""
    new_api_token_hash = ""
    old_flow = None
    new_flow = None
    try:
        log(f"[{old_email}] 开始协议换绑")
        old_flow, old_result = _login_flow(account, proxy, log, keep_session=True, should_cancel=db.cancel_requested)
        client = ChangeEmailClient(old_flow, str(old_result.get("account_id") or ""), log)
        client.set_access_token(str(old_result.get("access_token") or ""))
        client.eligibility()
        new_email, new_api, new_api_token_hash = _domain_mailbox(db, log)
        # Register the one-time pickup credential before ChatGPT sends the verification mail.
        # The public pickup endpoint validates the token against this database row.
        db.persist_rebind_pending(new_email, new_api, new_api_token_hash)
        reader_account = MailAccount(email=new_email, password="", client_id="", refresh_token="", raw=f"{new_email}----{new_api}", mailbox_type="domain", mailbox_channel="domain_api", access_key=new_api)
        reader = DomainMailReader(reader_account, log)
        try:
            reader.connect()
            issued_after = time.time()
            log(f"[{old_email}] 已建立换绑邮箱取件监听，准备请求 ChatGPT 发送验证码")
            try:
                _begin_with_retry(client, new_email, log)
                log(f"[{old_email}] ChatGPT 换绑验证码请求已接受，等待新邮箱验证码")
            except RebindError as exc:
                if "重新认证" not in str(exc):
                    raise
                previous_flow = old_flow
                old_flow, old_result = _login_flow(account, proxy, log, keep_session=True, should_cancel=db.cancel_requested)
                try:
                    if previous_flow and previous_flow.session:
                        previous_flow.session.close()
                except Exception:
                    pass
                client = ChangeEmailClient(old_flow, str(old_result.get("account_id") or ""), log)
                client.set_access_token(str(old_result.get("access_token") or ""))
                _begin_with_retry(client, new_email, log)
                log(f"[{old_email}] 重新认证后已重新提交换绑验证码请求，等待新邮箱验证码")
            code = _wait_for_rebind_code(reader, client, new_email, issued_after, log)
        finally:
            reader.close()
        client.verify(new_email, code)
        log(f"[{old_email}] 已向 ChatGPT 提交换绑邮箱验证码")
        new_account = MailAccount(email=new_email, password="", client_id="", refresh_token="", raw=f"{new_email}----{new_api}", mailbox_type="domain", mailbox_channel="domain_api", access_key=new_api, chatgpt_password=account.chatgpt_password, totp_secret=account.totp_secret)
        new_flow, new_result = _login_flow(new_account, proxy, log, keep_session=False, should_cancel=db.cancel_requested)
        if str(new_result.get("access_token") or "").strip() == "":
            raise RebindError("换绑后重新登录未返回新的 Access Token")
        if not str(new_result.get("refresh_token") or "").strip() and account.openai_rt:
            new_result["refresh_token"] = account.openai_rt
        db.persist_rebind(old_email, new_email, new_api, new_api_token_hash, new_result)
        log(f"[{old_email}] 换绑成功：{new_email}")
        return {"email": old_email, "new_email": new_email, "status": "success"}
    except Exception as exc:
        if new_email and new_api:
            cfg = db.get_config("domain_mailbox")
            try:
                db.persist_rebind_failure(old_email, new_email, new_api, new_api_token_hash, str(exc))
            except Exception as persist_exc:
                log(f"[{old_email}] 保存失败邮箱记录失败：{persist_exc}")
            try:
                if cleanup_failed_mailbox(db, cfg, new_email, new_api_token_hash, log):
                    log(f"[{old_email}] 换绑失败邮箱已按配置清理：{new_email}")
                else:
                    log(f"[{old_email}] 换绑失败邮箱已保存到自建域名邮箱池：{new_email}")
            except Exception as cleanup_exc:
                log(f"[{old_email}] 失败邮箱清理失败：{cleanup_exc}")
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
