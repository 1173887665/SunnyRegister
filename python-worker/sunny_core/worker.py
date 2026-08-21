from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Lock
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .agent_identity import AgentIdentityUnavailableError, create_agent_identity_auth
from .browser_traffic import ProxyTrafficMeter, use_traffic_meter
from .db import SunnyDB, SunnyTaskCancelled, now_sql
from .firefox_sms import FIREFOX_RELEASE_DELAY_SECONDS, FireFoxSMSClient
from .luban_sms import LubanSMSClient
from .mailbox import account_from_row, parse_account_line
from .openai_auth import TaskCancelledError, login_or_register, refresh_openai_access_token
from .phone_pool import read_sms_candidates, wait_sms_code
from .protocol_auth import ProtocolChallengeRequired, ProtocolRegistrationError, login_or_register_protocol
from .login_secret import setup_login_secret, setup_login_secret_protocol
from .proxy import build_proxy, proxy_target_tls_check, redact_proxy_url
from .smsbower import SMSBowerClient
from .smspool import SMSPOOL_CODE_TIMEOUT_SECONDS, SMSPoolClient

REGISTER_ONLY = "register_only"
CODEX_PHONE_BIND = "codex_phone_bind"
IMPORT_REVERSE_PROXY = "import_reverse_proxy"
AGENT_IDENTITY_REVERSE_PROXY = "agent_identity_reverse_proxy"

_REGISTRATION_PROGRESS_STEPS = {
    "initializing": 1,
    "proxy_ready": 2,
    "browser_started": 3,
    "protocol_started": 3,
    "email_submitted": 4,
    "email_verified": 5,
    "auth_completed": 6,
    "registered": 7,
    "phone_started": 8,
    "phone_code_received": 9,
    "phone_bound": 10,
    "reverse_importing": 11,
    "reverse_imported": 12,
    "agent_identity_importing": 8,
    "agent_identity_imported": 9,
    "login_secret_started": 1,
    "login_secret_password": 2,
    "login_secret_2fa": 3,
    "login_secret_completed": 4,
    "login_secret_failed": 4,
}

_REGISTRATION_STAGE_TOTALS = {
    REGISTER_ONLY: 7,
    CODEX_PHONE_BIND: 10,
    IMPORT_REVERSE_PROXY: 12,
    AGENT_IDENTITY_REVERSE_PROXY: 9,
}

_MAILBOX_PROGRESS_RANK = {
    "未注册": 0,
    "已注册": 1,
    "registered": 1,
    "已接码": 2,
    "phone_bound": 2,
    "已反代": 3,
    "reverse_proxied": 3,
}


class _ProtocolBatchPolicy:
    """Skip repeated protocol attempts after this batch proves they require a browser."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._protocol_attempts = 0
        self._browser_challenges = 0

    def record_challenge(self) -> None:
        with self._lock:
            self._protocol_attempts += 1
            self._browser_challenges += 1

    def record_success(self) -> None:
        with self._lock:
            self._protocol_attempts += 1

    def should_start_in_browser(self) -> bool:
        with self._lock:
            return (
                self._protocol_attempts >= 2
                and self._browser_challenges >= 2
                and self._browser_challenges * 4 >= self._protocol_attempts * 3
            )


def _is_retryable_protocol_transport_error(error: Exception) -> bool:
    message = str(error or "").lower()
    return any(marker in message for marker in (
        "curl: (28)",
        "curl: (35)",
        "connection reset by peer",
        "recv failure",
        "operation timed out",
        "unexpected_eof_while_reading",
        "unexpected eof while reading",
    ))

_DEFAULT_SUB2API_MODELS = (
    "codex-auto-review", "gpt-5.4", "gpt-5.4-mini", "gpt-5.5", "gpt-5.6",
    "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-image-1.5", "gpt-image-2",
)


def _container_host_proxy(proxy_url: str) -> str:
    """Route localhost proxy settings to the Docker host when containerized."""
    if os.getenv("SUNNY_CONTAINERIZED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return proxy_url
    try:
        parsed = urlsplit(proxy_url)
        if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
            return proxy_url
        auth = ""
        if "@" in parsed.netloc:
            auth = parsed.netloc.rsplit("@", 1)[0] + "@"
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, f"{auth}host.docker.internal{port}", parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return proxy_url


def _highest_mailbox_progress(current: str, candidate: str) -> str:
    current = str(current or "").strip()
    candidate = str(candidate or "").strip()
    current_rank = _MAILBOX_PROGRESS_RANK.get(current, -1)
    candidate_rank = _MAILBOX_PROGRESS_RANK.get(candidate, -1)
    return current if current_rank >= candidate_rank and current_rank >= 0 else candidate


def _account_status_for_mailbox(status: str) -> str:
    if status == "已反代":
        return "reverse_proxied"
    if status == "已接码":
        return "phone_bound"
    return "registered"


def _ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            n = int(item)
            if n > 0:
                out.append(n)
        except Exception:
            pass
    return out


def _stage(payload: dict[str, Any]) -> str:
    value = str(payload.get("registration_stage") or payload.get("stage") or REGISTER_ONLY).strip().lower()
    return value if value in {REGISTER_ONLY, CODEX_PHONE_BIND, IMPORT_REVERSE_PROXY, AGENT_IDENTITY_REVERSE_PROXY} else REGISTER_ONLY


def _stage_label(stage: str) -> str:
    return {
        REGISTER_ONLY: "仅注册ChatGPT",
        CODEX_PHONE_BIND: "Codex接码绑定",
        IMPORT_REVERSE_PROXY: "导入反代平台",
        AGENT_IDENTITY_REVERSE_PROXY: "绕过接码导入反代平台",
    }.get(stage, stage)


def _registration_stage_total(stage: str) -> int:
    return _REGISTRATION_STAGE_TOTALS.get(stage, _REGISTRATION_STAGE_TOTALS[REGISTER_ONLY])


def _emit_registration_progress(
    db: SunnyDB,
    email: str,
    stage: str,
    checkpoint: str,
    *,
    state: str = "running",
    error: str = "",
    setup_login_secret: bool = False,
) -> None:
    base_total = _registration_stage_total(stage)
    total = base_total + (4 if setup_login_secret else 0)
    if setup_login_secret and checkpoint.startswith("login_secret_"):
        current = base_total + min(4, max(0, _REGISTRATION_PROGRESS_STEPS.get(checkpoint, 0)))
    else:
        current = min(base_total, max(0, _REGISTRATION_PROGRESS_STEPS.get(checkpoint, 0)))
    db.event(
        f"[{email}] registration progress {current}/{total}: {checkpoint}",
        level="error" if state == "abnormal" else "info",
        typ="registration_progress",
        detail={
            "scope": "selected",
            "progress_type": "account_registration",
            "email": email,
            "stage": stage,
            "checkpoint": checkpoint,
            "current": current,
            "total": total,
            "state": state,
            "error": str(error or "")[:500],
        },
    )


def _emit_renewal_progress(
    db: SunnyDB,
    email: str,
    current: int,
    total: int,
    checkpoint: str,
    *,
    state: str = "running",
    error: str = "",
) -> None:
    safe_total = max(1, int(total or 1))
    safe_current = min(safe_total, max(0, int(current or 0)))
    db.event(
        f"[{email}] access token renewal progress {safe_current}/{safe_total}: {checkpoint}",
        level="error" if state == "failed" else "info",
        typ="renewal_progress",
        detail={
            "scope": "selected",
            "progress_type": "access_token_renewal",
            "email": email,
            "checkpoint": checkpoint,
            "current": safe_current,
            "total": safe_total,
            "state": state,
            "error": str(error or "")[:500],
        },
    )


def _account_event(
    db: SunnyDB,
    email: str,
    module: str,
    action: str,
    message: str,
    level: str = "info",
    detail: dict[str, Any] | None = None,
    *,
    account_id: int = 0,
    mailbox_id: int = 0,
    operation_id: str = "",
) -> None:
    writer = getattr(db, "account_event", None)
    if callable(writer):
        writer(
            email, module, action, message, level, detail,
            account_id=account_id, mailbox_id=mailbox_id, operation_id=operation_id,
        )
        return
    event_detail = dict(detail or {})
    event_detail.update({"email": email, "scope": "account", "module": module, "action": action})
    if account_id:
        event_detail["account_id"] = account_id
    if mailbox_id:
        event_detail["mailbox_id"] = mailbox_id
    if operation_id:
        event_detail["operation_id"] = operation_id
    db.event(message, level, detail=event_detail)


def _is_cancel_exception(exc: BaseException) -> bool:
    return isinstance(exc, (SunnyTaskCancelled, TaskCancelledError)) or "Task cancelled by user" in str(exc)


def _is_account_deactivated(error: Any) -> bool:
    text = str(error or "").strip().lower()
    return "account_deactivated" in text or "account because it has been deleted or deactivated" in text


def _is_otp_security_context_failure(error: Any) -> bool:
    """Return true only for a rejected OTP request caused by auth proof context.

    A retry must start a new authorization session and obtain a new OTP. Reusing
    the old code is intentionally excluded because it can consume the upstream
    attempt limit.
    """
    text = str(error or "").strip().lower()
    if _is_account_deactivated(text):
        return False
    otp_request = "emailotpvalidate" in text or "email-otp/validate" in text
    security_rejection = any(
        marker in text
        for marker in (
            "http 403",
            "resp 403",
            "cloudflare",
            "sentinel_required",
            "proof_required",
            "challenge_required",
        )
    )
    return otp_request and security_rejection


def _raw_mailboxes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, line in enumerate(str(payload.get("mailbox_lines") or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        acc = parse_account_line(line)
        rows.append({
            "id": 0 - idx,
            "email": acc.email,
            "password": acc.password,
            "client_id": acc.client_id,
            "refresh_token": acc.refresh_token,
            "openai_rt": acc.openai_rt,
            "raw": acc.raw,
            "account_type": acc.account_type,
            "status": "未注册",
        })
    return rows


def _proxy_pool_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_pool = payload.get("proxy_pool")
    pool_items = raw_pool if isinstance(raw_pool, list) else []
    raw_ids = payload.get("proxy_ids")
    proxy_ids = raw_ids if isinstance(raw_ids, list) else []
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(pool_items):
        stored_address = build_proxy("", str(item or "")).url
        if not stored_address:
            continue
        try:
            proxy_id = max(0, int(proxy_ids[index])) if index < len(proxy_ids) else 0
        except (TypeError, ValueError):
            proxy_id = 0
        candidates.append({
            "id": proxy_id,
            "address": stored_address,
            "register": _container_host_proxy(stored_address),
        })
    return candidates


def _proxy_snapshot(payload: dict[str, Any], slot: int = 0) -> dict[str, Any]:
    if payload.get("proxy_enabled") is False:
        system_proxy = str(payload.get("system_proxy") or "").strip()
        normalized_system_proxy = _container_host_proxy(build_proxy("", system_proxy).url)
        return {"register": normalized_system_proxy, "mode": "system_proxy" if normalized_system_proxy else "direct", "local_proxy": ""}
    base = str(payload.get("proxy") or "").strip()
    candidates = _proxy_pool_candidates(payload)
    if candidates:
        selected = candidates[max(0, int(slot)) % len(candidates)]
        register_proxy = selected["register"]
        proxy_id = selected["id"]
    else:
        register_proxy = _container_host_proxy(build_proxy("", str(payload.get("register_proxy") or base)).url)
        proxy_id = 0
    local_proxy = _container_host_proxy(build_proxy(str(payload.get("local_proxy") or ""), "").url)
    return {"register": register_proxy, "mode": "proxy_pool", "local_proxy": local_proxy, "proxy_id": proxy_id}


def _auxiliary_proxy(payload: dict[str, Any], proxies: dict[str, Any]) -> str:
    """Return the auxiliary route; empty means direct server egress."""
    if payload.get("proxy_all_traffic") is True:
        return str(proxies.get("register") or "")
    return ""


def _mailbox_proxy_for_task(
    payload: dict[str, Any],
    proxies: dict[str, Any],
    auxiliary_proxy: str,
    mailbox_type: str,
) -> str:
    if auxiliary_proxy:
        return auxiliary_proxy
    if payload.get("access_token_renewal") is True and str(mailbox_type or "").strip().lower() == "apple":
        return str(proxies.get("register") or "")
    return ""


def _prepare_register_proxy(db: SunnyDB, payload: dict[str, Any], email: str, slot: int = 0) -> dict[str, Any]:
    proxies = _proxy_snapshot(payload, slot)
    proxy = proxies.get("register", "")
    if not proxy or proxies.get("mode") != "proxy_pool":
        return proxies

    candidates = _proxy_pool_candidates(payload)
    if candidates:
        start = max(0, int(slot)) % len(candidates)
        candidates = candidates[start:] + candidates[:start]
        fallbacks = candidates[1:]
        random.SystemRandom().shuffle(fallbacks)
        candidates = candidates[:1] + fallbacks
    else:
        candidates = [{"id": 0, "address": proxy, "register": proxy}]

    failures: list[str] = []
    for attempt, candidate in enumerate(candidates, start=1):
        proxy_id = int(candidate.get("id") or 0)
        candidate_proxy = str(candidate.get("register") or "")
        if proxy_id > 0 and not db.proxy_is_usable(proxy_id):
            db.event(
                f"[{email}] [代理] 跳过已被其他任务标记为失效的代理：{redact_proxy_url(candidate_proxy)}",
                "warning",
                detail={"email": email, "scope": "selected", "proxy": candidate_proxy, "proxy_id": proxy_id, "proxy_mode": "proxy_pool", "proxy_skipped": True},
            )
            continue
        check = proxy_target_tls_check(candidate_proxy, timeout=10)
        if check.get("ok"):
            selected = {**proxies, "register": candidate_proxy, "proxy_id": proxy_id}
            db.event(
                f"[{email}] [代理] 代理 HTTPS 隧道预检通过：{redact_proxy_url(candidate_proxy)}，延迟 {check.get('latency_ms', 0)}ms",
                detail={"email": email, "scope": "selected", "proxy": candidate_proxy, "proxy_id": proxy_id, "proxy_mode": selected.get("mode"), "proxy_precheck": check, "proxy_attempt": attempt},
            )
            return selected
        err = str(check.get("error") or "unknown error")
        failures.append(f"{redact_proxy_url(candidate_proxy)}: {err}")
        transition = "仅本次跳过并切换下一条，不修改代理池状态"
        db.event(
            f"[{email}] [代理] 代理无法建立到 chatgpt.com:443 的 HTTPS 隧道，{transition}：{redact_proxy_url(candidate_proxy)}；原因：{err}",
            "warning",
            detail={"email": email, "scope": "selected", "proxy": candidate_proxy, "proxy_id": proxy_id, "proxy_mode": "proxy_pool", "proxy_precheck": check, "proxy_attempt": attempt, "proxy_pool_status_unchanged": True},
        )

    local_proxy = proxies.get("local_proxy", "")
    attempted_proxies = {str(candidate.get("register") or "") for candidate in candidates}
    if local_proxy and local_proxy not in attempted_proxies:
        local_check = proxy_target_tls_check(local_proxy, timeout=10)
        if local_check.get("ok"):
            db.event(
                f"[{email}] [代理] 代理池候选均不可用，已自动回退到本地代理出口：{redact_proxy_url(local_proxy)}。",
                "warning",
                detail={"email": email, "scope": "selected", "proxy": local_proxy, "proxy_mode": "local_proxy_fallback", "proxy_precheck": local_check},
            )
            return {"register": local_proxy, "mode": "local_proxy_fallback", "local_proxy": local_proxy}
        db.event(
            f"[{email}] [代理] 本地代理出口也未通过 HTTPS 隧道预检：{redact_proxy_url(local_proxy)}；原因：{local_check.get('error') or 'unknown error'}",
            "warning",
            detail={"email": email, "scope": "selected", "proxy": local_proxy, "proxy_mode": "local_proxy_fallback", "proxy_precheck": local_check},
        )
    failure_summary = "；".join(failures[-3:]) or "任务快照中的代理均已失效"
    raise RuntimeError(f"代理池中没有可用于 ChatGPT 注册链路的代理；{failure_summary}")


def _log_proxy_startup(db: SunnyDB, payload: dict[str, Any]) -> None:
    stats = payload.get("proxy_stats") if isinstance(payload.get("proxy_stats"), dict) else {}
    total = int(stats.get("total") or 0)
    enabled = int(stats.get("enabled") or 0)
    disabled = int(stats.get("disabled") or 0)
    invalid = int(stats.get("invalid") or 0)
    if payload.get("proxy_enabled") is False:
        system_proxy = _proxy_snapshot(payload).get("register", "")
        db.event(
            f"[代理] 代理池开关：关闭；注册机将使用服务器系统出口{'代理：' + system_proxy if system_proxy else '直连'}。代理池总数 {total}，启用 {enabled}，停用 {disabled}，失效 {invalid}",
            detail={"scope": "global", "proxy_enabled": False, "proxy_stats": stats, "system_proxy": system_proxy},
        )
        return
    proxy = _proxy_snapshot(payload).get("register", "")
    proxy_pool_size = len(payload.get("proxy_pool") or [])
    db.event(
        f"[代理] 代理开关：开启；代理池总数 {total}，启用 {enabled}，停用 {disabled}，失效 {invalid}；本任务快照可分配 {proxy_pool_size or (1 if proxy else 0)} 个代理",
        detail={"scope": "global", "proxy_enabled": True, "proxy_stats": stats, "proxy_pool_size": proxy_pool_size},
    )
    if proxy:
        redacted = redact_proxy_url(proxy)
        db.event(f"[代理] 注册/登录请求将按邮箱轮询使用代理池，首个出口：{redacted}", detail={"scope": "global", "proxy": redacted})
    else:
        db.event("[代理] 未获取到可用代理，注册任务将停止或回退到后端校验结果", "warning", detail={"scope": "global"})


def _choose_mailboxes(db: SunnyDB, payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _raw_mailboxes(payload)
    if raw:
        return raw
    ids = _ids(payload.get("mailbox_ids"))
    return db.fetch_mailboxes(ids or None, int(payload.get("count") or 0))


def _phone_provider(db: SunnyDB, email: str):
    active: dict[str, Any] = {}

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active
        if action == "next":
            phone = db.reserve_phone()
            if not phone:
                return None
            try:
                phone["seen_sms_keys"] = [item["key"] for item in read_sms_candidates(str(phone.get("sms_url") or ""))]
            except Exception as exc:
                if phone.get("id"):
                    db.mark_phone_error(int(phone["id"]), f"无法建立短信基线: {exc}")
                db.event(
                    f"[{email}] [接码] 自建收码接口基线读取失败，已拒绝该号码：{exc}",
                    "warning",
                    detail={"email": email, "scope": "selected", "sms_provider": "local"},
                )
                raise RuntimeError(f"自建收码接口无法建立短信基线: {exc}") from exc
            active = phone
            db.event(f"[{email}] [接码] 已从接码配置分配手机号 {phone.get('number')}", detail={"email": email, "scope": "selected"})
            return phone
        if action == "code":
            phone = payload or active
            return wait_sms_code(
                str(phone.get("number") or ""),
                str(phone.get("sms_url") or ""),
                timeout=180,
                log=lambda m: db.event(f"[{email}] {m}", detail={"email": email, "scope": "selected"}),
                seen_keys=set(phone.get("seen_sms_keys") or []),
            )
        if action == "success":
            phone = payload or active
            if phone and phone.get("id"):
                db.mark_phone_success(int(phone["id"]), str(phone.get("code") or ""))
            return True
        if action == "bad":
            phone = payload or active
            if phone and phone.get("id"):
                db.mark_phone_error(int(phone["id"]), str(phone.get("error") or "phone verification failed"))
            return True
        return None

    return provider


def _sms_country_metadata(db: SunnyDB, option: dict[str, Any] | None, country: str = "", dial_code: str = "") -> dict[str, str]:
    loader = getattr(db, "sms_provider_option_extra", None)
    extra = loader(option) if callable(loader) else {}
    extra = extra if isinstance(extra, dict) else {}
    title = str(extra.get("Country_Title") or "").strip()
    title_parts = [part.strip() for part in title.split("/") if part.strip()]
    country_name = str(
        extra.get("name")
        or extra.get("eng")
        or extra.get("country_name")
        or (title_parts[-1] if len(title_parts) > 1 else "")
        or (option or {}).get("label")
        or ""
    ).strip()
    country_iso = str(
        extra.get("short_name")
        or extra.get("iso2")
        or extra.get("iso")
        or extra.get("Country_ID")
        or country
    ).strip()
    country_code = str(
        dial_code
        or extra.get("cc")
        or extra.get("country_code")
        or extra.get("dial_code")
        or extra.get("Country_Area")
        or ""
    ).strip().lstrip("+")
    return {
        "country": str(country or "").strip(),
        "country_iso": country_iso,
        "country_name": country_name,
        "country_code": country_code,
    }


def _smsbower_provider(db: SunnyDB, email: str, proxy_url: str = "", country_override: str = ""):
    active: dict[str, Any] = {}
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    phone_cfg = db.get_config("phone")
    country_value = str(country_override or phone_cfg.get("smsbower_default_country") or "187").strip()
    country_option = db.resolve_sms_provider_option("smsbower", "country", country_value)
    resolved_country = str((country_option or {}).get("value") or country_value)
    phone_cfg = {**phone_cfg, "smsbower_default_country": resolved_country}
    country_metadata = _sms_country_metadata(db, country_option, resolved_country)
    client = SMSBowerClient(phone_cfg, proxies=proxies)

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active
        if action == "next":
            activation = client.get_number()
            active = {
                "provider": "smsbower",
                "activation_id": activation.activation_id,
                "number": activation.number,
                **country_metadata,
            }
            db.event(
                f"[{email}] [接码] 已从 SMSBower 获取手机号 {activation.number}，激活 ID {activation.activation_id}",
                detail={"email": email, "scope": "selected", "sms_provider": "smsbower"},
            )
            return active
        if action == "code":
            phone = payload or active
            activation_id = str(phone.get("activation_id") or "")
            return client.wait_code(
                activation_id,
                timeout=180,
                log=lambda m: db.event(f"[{email}] [接码] {m}", detail={"email": email, "scope": "selected", "sms_provider": "smsbower"}),
            )
        if action == "success":
            phone = payload or active
            activation_id = str(phone.get("activation_id") or "")
            client.finish(activation_id)
            db.event(f"[{email}] [接码] SMSBower 激活已完成", detail={"email": email, "scope": "selected", "sms_provider": "smsbower"})
            return True
        if action == "bad":
            phone = payload or active
            activation_id = str(phone.get("activation_id") or "")
            try:
                client.cancel(activation_id)
            finally:
                db.event(f"[{email}] [接码] SMSBower 激活已取消", "warning", detail={"email": email, "scope": "selected", "sms_provider": "smsbower"})
            return True
        return None

    return provider


def _luban_provider(db: SunnyDB, email: str, proxy_url: str = ""):
    active: dict[str, Any] = {}
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    client = LubanSMSClient(db.get_config("phone"), proxies=proxies)

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active
        if action == "next":
            activation = client.get_number()
            active = {"provider": "luban", "activation_id": activation.request_id, "number": activation.number}
            db.event(
                f"[{email}] [接码] 已从 LubanSMS 获取手机号 {activation.number}",
                detail={"email": email, "scope": "selected", "sms_provider": "luban"},
            )
            return active
        if action == "code":
            phone = payload or active
            return client.wait_code(
                str(phone.get("activation_id") or ""),
                timeout=180,
                log=lambda message: db.event(f"[{email}] [接码] {message}", detail={"email": email, "scope": "selected", "sms_provider": "luban"}),
            )
        if action == "success":
            db.event(f"[{email}] [接码] LubanSMS 接码完成", detail={"email": email, "scope": "selected", "sms_provider": "luban"})
            return True
        if action == "bad":
            phone = payload or active
            client.release(str(phone.get("activation_id") or ""))
            active = {}
            db.event(f"[{email}] [接码] LubanSMS 号码已拒绝释放", "warning", detail={"email": email, "scope": "selected", "sms_provider": "luban"})
            return True
        return None

    return provider


def _smspool_provider(db: SunnyDB, email: str, proxy_url: str = "", country_override: str = ""):
    active: dict[str, Any] = {}
    reuse_checked = False
    new_number_attempts = 0
    max_new_number_attempts = 3
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    phone_cfg = db.get_config("phone")
    country_value = str(country_override or phone_cfg.get("smspool_default_country") or "1").strip()
    country_option = db.resolve_sms_provider_option("smspool", "country", country_value)
    resolved_country = str((country_option or {}).get("value") or country_value or "1")
    service_value = str(phone_cfg.get("smspool_default_service") or "OpenAI").strip()
    service_option = db.resolve_sms_provider_option("smspool", "service", service_value, resolved_country)
    resolved_service = str((service_option or {}).get("value") or service_value)
    if resolved_country != country_value or resolved_service != service_value:
        phone_cfg = {
            **phone_cfg,
            "smspool_default_country": resolved_country,
            "smspool_default_service": resolved_service,
        }
        db.event(
            f"[{email}] [接码] 本次任务已将 SMSPool 配置解析为接口 ID：country={resolved_country}，service={resolved_service}",
            detail={"email": email, "scope": "selected", "sms_provider": "smspool", "country": resolved_country, "service": resolved_service},
        )
    country_metadata = _sms_country_metadata(db, country_option, resolved_country)
    client = SMSPoolClient(phone_cfg, proxies=proxies)

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active, reuse_checked, new_number_attempts
        if action == "next":
            db.event(
                f"[{email}] [接码] 准备向 SMSPool 申请手机号：country={client.country}，service={client.service}，pool={client.pool or '-'}，max_price={client.max_price}",
                detail={"email": email, "scope": "selected", "sms_provider": "smspool", "country": client.country, "service": client.service, "pool": client.pool, "max_price": client.max_price},
            )
            activation = None
            reused = False
            if not reuse_checked:
                reuse_checked = True
                try:
                    reusable = client.latest_reusable_order()
                except Exception as exc:
                    reusable = None
                    db.event(
                        f"[{email}] [接码] SMSPool orders_new 查询失败，本次跳过号码复用：{exc}",
                        "warning",
                        detail={"email": email, "scope": "selected", "sms_provider": "smspool", "reuse": True},
                    )
                if reusable:
                    db.event(
                        f"[{email}] [接码] SMSPool 尝试复用 orders_new 中最新订单（id={reusable.id}）的手机号 {reusable.number}",
                        detail={"email": email, "scope": "selected", "sms_provider": "smspool", "reuse": True, "orders_new_id": reusable.id},
                    )
                    try:
                        activation = client.get_number(preferred_number=reusable.number)
                        reused = True
                    except Exception as exc:
                        db.mark_sms_provider_number_error("smspool", reusable.number, str(exc))
                        db.event(
                            f"[{email}] [接码] SMSPool 最新手机号复用失败，将申请新号码：{exc}",
                            "warning",
                            detail={"email": email, "scope": "selected", "sms_provider": "smspool", "reuse": True, "orders_new_id": reusable.id},
                        )

            while activation is None and new_number_attempts < max_new_number_attempts:
                new_number_attempts += 1
                try:
                    activation = client.get_number()
                except Exception as exc:
                    db.event(
                        f"[{email}] [接码] SMSPool 第 {new_number_attempts}/{max_new_number_attempts} 次申请新号码失败：{exc}",
                        "warning",
                        detail={"email": email, "scope": "selected", "sms_provider": "smspool", "new_number_attempt": new_number_attempts},
                    )
            if activation is None:
                db.event(
                    f"[{email}] [接码] SMSPool 已用完 {max_new_number_attempts} 次新号码机会，停止使用该供应商",
                    "warning",
                    detail={"email": email, "scope": "selected", "sms_provider": "smspool", "exhausted": True},
                )
                return None
            active = {
                "provider": "smspool",
                "order_id": activation.order_id,
                "activation_id": activation.order_id,
                "number": activation.number,
                "token": activation.token,
                **country_metadata,
                "reused": reused,
                "new_number_attempt": 0 if reused else new_number_attempts,
            }
            db.record_sms_provider_number(
                "smspool",
                activation.number,
                country=client.country,
                service=client.service,
                pool=client.pool,
                order_id=activation.order_id,
                token=activation.token,
            )
            db.event(
                f"[{email}] [接码] 已从 SMSPool 获取手机号 {activation.number}，订单 ID {activation.order_id}"
                + ("（复用最新订单号码）" if reused else f"（新号码 {new_number_attempts}/{max_new_number_attempts}）"),
                detail={"email": email, "scope": "selected", "sms_provider": "smspool", "reuse": reused, "new_number_attempt": 0 if reused else new_number_attempts},
            )
            return active
        if action == "code":
            phone = payload or active
            order_id = str(phone.get("order_id") or phone.get("activation_id") or "")
            return client.wait_code(
                order_id,
                timeout=SMSPOOL_CODE_TIMEOUT_SECONDS,
                log=lambda m: db.event(f"[{email}] [接码] {m}", detail={"email": email, "scope": "selected", "sms_provider": "smspool"}),
            )
        if action == "success":
            phone = payload or active
            db.mark_sms_provider_number_success("smspool", str(phone.get("number") or ""), str(phone.get("code") or ""))
            db.event(f"[{email}] [接码] SMSPool 接码订单已完成，手机号进入 5 小时冷却后可复用", detail={"email": email, "scope": "selected", "sms_provider": "smspool"})
            return True
        if action == "bad":
            phone = payload or active
            order_id = str(phone.get("order_id") or phone.get("activation_id") or "")
            cancel_error = ""
            try:
                client.cancel(order_id)
            except Exception as exc:
                cancel_error = str(exc)
            db.mark_sms_provider_number_error("smspool", str(phone.get("number") or ""), str(phone.get("error") or "SMSPool order failed"))
            active = {}
            retry_same_provider = new_number_attempts < max_new_number_attempts
            if cancel_error:
                db.event(
                    f"[{email}] [接码] SMSPool 订单取消失败，但仍继续换号：{cancel_error}",
                    "warning",
                    detail={"email": email, "scope": "selected", "sms_provider": "smspool", "order_id": order_id},
                )
            else:
                db.event(
                    f"[{email}] [接码] SMSPool 接码订单已取消，将更换手机号",
                    "warning",
                    detail={"email": email, "scope": "selected", "sms_provider": "smspool", "order_id": order_id},
                )
            if not retry_same_provider:
                db.event(
                    f"[{email}] [接码] SMSPool 三次新号码均未完成接码，放弃该供应商",
                    "warning",
                    detail={"email": email, "scope": "selected", "sms_provider": "smspool", "exhausted": True},
                )
            return {"retry_same_provider": retry_same_provider}
        return None

    return provider


def _firefox_provider(db: SunnyDB, email: str, proxy_url: str = "", country_override: str = ""):
    active: dict[str, Any] = {}
    number_attempts = 0
    max_number_attempts = 3
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    phone_cfg = db.get_config("phone")
    country_value = str(country_override or phone_cfg.get("firefox_default_country") or "").strip()
    country_option = db.resolve_sms_provider_option("firefox", "country", country_value)
    resolved_country = str((country_option or {}).get("value") or country_value)
    country_metadata = _sms_country_metadata(db, country_option, resolved_country)
    service_value = str(phone_cfg.get("firefox_default_service") or "1096").strip()
    service_option = db.resolve_sms_provider_option("firefox", "service", service_value, resolved_country)
    resolved_service = str((service_option or {}).get("value") or service_value)
    phone_cfg = {
        **phone_cfg,
        "firefox_default_country": resolved_country,
        "firefox_default_service": resolved_service,
    }
    client = FireFoxSMSClient(phone_cfg, proxies=proxies)

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active, number_attempts
        if action == "next":
            if number_attempts >= max_number_attempts:
                return None
            number_attempts += 1
            db.event(
                f"[{email}] [接码] 准备向 FireFox 申请手机号：country={client.country}，service={client.service}，max_price={client.max_price}，quantity=1",
                detail={"email": email, "scope": "selected", "sms_provider": "firefox", "country": client.country, "service": client.service, "max_price": client.max_price, "quantity": 1},
            )
            activation = client.get_number()
            active = {
                "provider": "firefox",
                "pkey": activation.pkey,
                "activation_id": activation.pkey,
                "number": activation.number,
                **{
                    **country_metadata,
                    "country": activation.country or country_metadata["country"] or client.country,
                    "country_code": activation.country_code or country_metadata["country_code"],
                },
                "new_number_attempt": number_attempts,
            }
            db.record_sms_provider_number(
                "firefox",
                activation.number,
                country=activation.country or client.country,
                service=client.service,
                order_id=activation.pkey,
            )
            db.event(
                f"[{email}] [接码] 已从 FireFox 获取手机号 {activation.number}，pkey {activation.pkey}（{number_attempts}/{max_number_attempts}）",
                detail={"email": email, "scope": "selected", "sms_provider": "firefox", "pkey": activation.pkey, "number_attempt": number_attempts},
            )
            return active
        if action == "code":
            phone = payload or active
            pkey = str(phone.get("pkey") or phone.get("activation_id") or "")
            return client.wait_code(
                pkey,
                timeout=180,
                log=lambda message: db.event(
                    f"[{email}] [接码] {message}",
                    detail={"email": email, "scope": "selected", "sms_provider": "firefox"},
                ),
            )
        if action == "success":
            phone = payload or active
            db.mark_sms_provider_number_success("firefox", str(phone.get("number") or ""), str(phone.get("code") or ""))
            db.event(f"[{email}] [接码] FireFox 手机号接码完成", detail={"email": email, "scope": "selected", "sms_provider": "firefox"})
            return True
        if action == "bad":
            phone = payload or active
            pkey = str(phone.get("pkey") or phone.get("activation_id") or "")
            client.release_later(pkey, FIREFOX_RELEASE_DELAY_SECONDS)
            db.mark_sms_provider_number_error("firefox", str(phone.get("number") or ""), str(phone.get("error") or "FireFox phone verification failed"))
            active = {}
            retry_same_provider = number_attempts < max_number_attempts
            db.event(
                f"[{email}] [接码] FireFox 当前号码不可用，已安排 {FIREFOX_RELEASE_DELAY_SECONDS} 秒后异步释放，"
                + ("立即申请下一个号码" if retry_same_provider else "三次号码均未完成接码，放弃该供应商"),
                "warning",
                detail={"email": email, "scope": "selected", "sms_provider": "firefox", "pkey": pkey, "retry_same_provider": retry_same_provider},
            )
            return {"retry_same_provider": retry_same_provider}
        return None

    return provider


def _combined_phone_provider(db: SunnyDB, email: str, proxy_url: str = "", execution_mode: str = "protocol"):
    background_us_only = str(execution_mode or "").strip().lower() == "background"
    candidates: list[tuple[str, Any]] = []
    if _provider_is_available(db, "luban"):
        candidates.append(("LubanSMS", lambda: _luban_provider(db, email, proxy_url)))
    if _provider_is_available(db, "smsbower"):
        candidates.append(("SMSBower", lambda: _smsbower_provider(db, email, proxy_url, "187" if background_us_only else "")))
    if _provider_is_available(db, "smspool"):
        candidates.append(("SMSPool", lambda: _smspool_provider(db, email, proxy_url, "1" if background_us_only else "")))
    if _provider_is_available(db, "firefox"):
        candidates.append(("FireFox", lambda: _firefox_provider(db, email, proxy_url, "usa" if background_us_only else "")))
    random.shuffle(candidates)
    if db.usable_phone_count() > 0:
        candidates.append(("自建手机号池", lambda: _phone_provider(db, email)))
    if not candidates:
        return None

    remaining = list(candidates)
    active_provider = None
    active_name = ""
    active_phone: dict[str, Any] = {}

    db.event(
        f"[{email}] [接码] 本次接码候选顺序：{' → '.join(name for name, _ in remaining)}（外部供应商随机，自建手机号池兜底）",
        detail={"email": email, "scope": "selected", "sms_provider": "combined", "candidate_order": [name for name, _ in remaining]},
    )
    if background_us_only:
        db.event(
            f"[{email}] [接码] 后台无头浏览器模式仅使用美国 +1 手机号；本次任务将外部供应商国家临时设为美国，不修改已保存配置",
            detail={"email": email, "scope": "selected", "sms_provider": "combined", "execution_mode": "background", "country_code": "1"},
        )

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active_provider, active_name, active_phone
        if action == "next":
            if active_provider:
                try:
                    phone = active_provider("next", _email, payload)
                    if phone:
                        active_phone = dict(phone)
                        active_phone["provider_name"] = active_name
                        if background_us_only and not str(active_phone.get("number") or "").strip().startswith("+1"):
                            active_provider("bad", _email, {**active_phone, "error": "后台无头浏览器模式只允许美国 +1 手机号"})
                            raise RuntimeError("后台无头浏览器模式只允许美国 +1 手机号")
                        return active_phone
                except Exception as exc:
                    db.event(
                        f"[{email}] [接码] {active_name} 无法继续获取手机号，切换下一个接码资源：{exc}",
                        "warning",
                        detail={"email": email, "scope": "selected", "sms_provider": active_name, "error": str(exc)},
                    )
                active_provider = None
                active_name = ""
                active_phone = {}
            while remaining:
                name, factory = remaining.pop(0)
                candidate_provider = None
                db.event(
                    f"[{email}] [接码] 正在尝试接码资源：{name}",
                    detail={"email": email, "scope": "selected", "sms_provider": name},
                )
                try:
                    candidate_provider = factory()
                    phone = candidate_provider("next", _email, payload)
                    if not phone:
                        raise RuntimeError("未获取到可用手机号")
                    active_provider = candidate_provider
                    active_name = name
                    active_phone = dict(phone)
                    active_phone["provider_name"] = name
                    if background_us_only and not str(active_phone.get("number") or "").strip().startswith("+1"):
                        candidate_provider("bad", _email, {**active_phone, "error": "后台无头浏览器模式只允许美国 +1 手机号"})
                        raise RuntimeError("后台无头浏览器模式只允许美国 +1 手机号")
                    return active_phone
                except Exception as exc:
                    if candidate_provider is not None and active_provider is candidate_provider:
                        active_provider = None
                        active_name = ""
                        active_phone = {}
                    db.event(
                        f"[{email}] [接码] {name} 无法获取手机号，继续尝试下一个接码资源：{exc}",
                        "warning",
                        detail={"email": email, "scope": "selected", "sms_provider": name, "error": str(exc)},
                    )
            db.event(
                f"[{email}] [接码] 所有外部接码供应商及自建手机号池均不可用，停止手机号绑定",
                "warning",
                detail={"email": email, "scope": "selected", "sms_provider": "combined", "exhausted": True},
            )
            return None
        if not active_provider:
            return None
        result = None
        try:
            result = active_provider(action, _email, payload or active_phone)
            return result
        finally:
            if action == "bad":
                retry_same_provider = isinstance(result, dict) and result.get("retry_same_provider") is True
                db.event(
                    f"[{email}] [接码] {active_name} 本次号码失败，"
                    + ("继续使用该供应商申请下一个号码" if retry_same_provider else "切换到下一个接码资源"),
                    "warning",
                    detail={"email": email, "scope": "selected", "sms_provider": active_name, "retry_same_provider": retry_same_provider},
                )
                active_phone = {}
                if not retry_same_provider:
                    active_provider = None
                    active_name = ""

    return provider


def _sub2api_group_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            n = int(item)
            if n > 0:
                out.append(n)
        except Exception:
            pass
    return out


def _sub2api_config(db: SunnyDB) -> tuple[dict[str, Any], str, str]:
    cfg = db.get_config("sub2api")
    if cfg.get("enabled") is False:
        raise RuntimeError("反代配置中的 sub2api 未启用")
    base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
    token = str(cfg.get("admin_token") or "").strip()
    if not base_url or not token:
        raise RuntimeError("请先在反代配置中填写 sub2api Base URL 和 Admin Token")
    return cfg, base_url, token


def _provider_is_available(db: SunnyDB, provider: str) -> bool:
    checker = getattr(db, f"{provider}_available", None)
    return bool(checker()) if callable(checker) else False


def _sub2api_secret_key(db: SunnyDB, email: str, session: dict[str, Any]) -> str:
    fetch_mailbox = getattr(db, "fetch_mailbox_by_email", None)
    if callable(fetch_mailbox):
        mailbox = fetch_mailbox(email)
        if isinstance(mailbox, dict):
            try:
                secret_key = str(account_from_row(mailbox).raw or "").strip()
            except (TypeError, ValueError):
                secret_key = str(mailbox.get("raw") or "").strip()
            if secret_key:
                return secret_key
    return str(session.get("raw_mailbox_line") or session.get("mailbox_raw") or "").strip()


def _sub2api_notes(db: SunnyDB, email: str, session: dict[str, Any]) -> str:
    lines: list[str] = []
    secret_key = _sub2api_secret_key(db, email, session)
    if secret_key:
        lines.append(f"邮箱凭证：{secret_key}")
    login_secret = _sub2api_login_secret(db, email, session)
    if login_secret:
        lines.append(f"密码2FA：{login_secret}")
    return "\n".join(lines)


def _import_sub2api(db: SunnyDB, email: str, account_id: int, session: dict[str, Any], proxy_url: str = "") -> dict[str, Any]:
    cfg, base_url, token = _sub2api_config(db)
    access_token = str(session.get("access_token") or "").strip()
    refresh_token = str(session.get("refresh_token") or session.get("openai_rt") or "").strip()
    if not access_token or not refresh_token:
        raise RuntimeError("当前账号缺少 Access Token 或 Refresh Token，无法导入 sub2api")
    token_record = session.get("token_record")
    if not isinstance(token_record, dict):
        token_record = {}
    credentials = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": session.get("id_token", ""),
        "email": email,
        "client_id": session.get("client_id") or token_record.get("client_id") or "app_EMoamEEZ73f0CkXaXp7hrann",
    }
    optional_credentials = {
        "chatgpt_account_id": session.get("chatgpt_account_id") or token_record.get("account_id"),
        "chatgpt_user_id": session.get("chatgpt_user_id") or token_record.get("chatgpt_user_id"),
        "organization_id": session.get("organization_id") or token_record.get("organization_id"),
        "plan_type": session.get("plan_type") or token_record.get("plan_type"),
        "expires_at": session.get("expires_at") or token_record.get("expires_at"),
    }
    credentials.update({key: value for key, value in optional_credentials.items() if value not in (None, "", 0)})
    model_mapping = session.get("model_mapping")
    if not isinstance(model_mapping, dict) or not model_mapping:
        configured_models = [model for model in (cfg.get("model_whitelist") or []) if isinstance(model, str) and model.strip()]
        model_mapping = {model: model for model in (configured_models or _DEFAULT_SUB2API_MODELS)}
    elif cfg.get("model_whitelist"):
        model_mapping = {str(model): str(model) for model in cfg.get("model_whitelist") if str(model).strip()}
    if model_mapping:
        credentials["model_mapping"] = model_mapping
    account_payload = {
        "name": f"{str(cfg.get('name_prefix') or '')}{email}",
        "notes": _sub2api_notes(db, email, session),
        "platform": "openai",
        "type": "oauth",
        "credentials": credentials,
        "extra": {"import_source": "sunnyregister_oauth_code", "email": email},
        "group_ids": _sub2api_group_ids(cfg.get("group_ids")),
        "concurrency": int(cfg.get("concurrency") or 3),
        "priority": int(cfg.get("priority") or 50),
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }
    if int(cfg.get("proxy_id") or 0) > 0:
        account_payload["proxy_id"] = int(cfg["proxy_id"])
    if int(cfg.get("load_factor") or 0) > 0:
        account_payload["load_factor"] = int(cfg["load_factor"])
    request_headers = {"x-api-key": token, "Idempotency-Key": f"sunny-{db.task_id}-{account_id}-{uuid.uuid4().hex[:8]}"}
    resp = None
    for attempt in range(2):
        try:
            resp = requests.post(
                f"{base_url}/api/v1/admin/accounts/batch",
                headers=request_headers,
                json={"accounts": [account_payload]},
                timeout=90,
                proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
            )
        except requests.RequestException:
            if attempt == 0:
                continue
            raise
        if attempt == 0 and (resp.status_code == 429 or resp.status_code >= 500):
            continue
        break
    if resp is None:
        raise RuntimeError("sub2api 导入请求未返回响应")
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"sub2api 导入失败: HTTP {resp.status_code} {resp.text[:500]}")
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    response_data = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if not isinstance(response_data, dict):
        response_data = {}
    succeeded = int(response_data.get("success") or response_data.get("succeeded") or response_data.get("created") or 0)
    failed = int(response_data.get("failed") or 0)
    remote_id = str(response_data.get("id") or "")
    confirmed = succeeded == 1 and failed == 0
    results = response_data.get("results")
    if not confirmed and isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            nested = item.get("account") if isinstance(item.get("account"), dict) else {}
            item_email = str(item.get("email") or item.get("account_email") or item.get("name") or nested.get("email") or nested.get("account_email") or nested.get("name") or "").strip().lower()
            item_status = str(item.get("status") or item.get("state") or "").strip().lower()
            item_ok = item.get("success") is True or item_status in {"success", "succeeded", "created", "imported"}
            if item_ok and item_email in {email.lower(), f"{str(cfg.get('name_prefix') or '')}{email}".lower()}:
                confirmed = True
                remote_id = str(item.get("id") or item.get("account_id") or item.get("remote_id") or nested.get("id") or nested.get("account_id") or nested.get("remote_id") or "")
                break
    if failed > 0 or not confirmed:
        raise RuntimeError(f"sub2api 批量导入未确认成功: {json.dumps(data, ensure_ascii=False)[:500]}")
    db.set_account_sub2api_status(email, "imported", remote_id)
    db.event(f"[{email}] [反代] 已根据反代配置导入 sub2api", detail={"email": email, "scope": "selected", "account_id": account_id})
    return data


def _sub2api_login_secret(db: SunnyDB, email: str, session: dict[str, Any]) -> str:
    mailbox = db.fetch_mailbox_by_email(email) or {}
    password = str(mailbox.get("chat_gpt_password") or mailbox.get("chatgpt_password") or "").strip()
    totp = str(mailbox.get("totp_secret") or "").strip()
    if password and totp:
        return f"{email}----{password}----{totp}"
    return ""


def _login_secret_result_message(result: dict[str, Any]) -> str:
    errors = "；".join(str(item) for item in (result.get("errors") or []) if str(item).strip()) or "未知原因"
    password_complete = bool(result.get("password"))
    totp_complete = bool(result.get("totp_secret"))
    access_token_refreshed = bool(result.get("access_token_refreshed"))
    if result.get("complete"):
        return "ChatGPT 密码、2FA 与最新 Access Token 已全部完成"
    if password_complete and totp_complete:
        at_status = "已更新" if access_token_refreshed else "更新未完成"
        return f"ChatGPT 密码与 2FA 已成功并保存，Access Token {at_status}：{errors}"
    if password_complete:
        return f"ChatGPT 密码已成功并保存，2FA 未完成，Access Token 未刷新：{errors}"
    if totp_complete:
        return f"ChatGPT 2FA 已保存，但密码未完成，Access Token 未刷新：{errors}"
    return f"ChatGPT 密码与 2FA 均未完成，Access Token 未刷新：{errors}"


def _import_sub2api_agent_identity(
    db: SunnyDB,
    email: str,
    account_id: int,
    session: dict[str, Any],
    proxy_url: str,
) -> dict[str, Any]:
    _cfg, base_url, token = _sub2api_config(db)
    access_token = str(session.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("当前账号没有 Access Token，无法创建 Agent Identity")
    try:
        auth_json = create_agent_identity_auth(
            access_token,
            email=email,
            plan_type=str(session.get("plan_type") or "free"),
            proxy_url=proxy_url,
            should_cancel=db.cancel_requested,
            log=lambda message: db.event(message, detail={"email": email, "scope": "selected"}),
        )
    except AgentIdentityUnavailableError as exc:
        refresh_token = str(session.get("refresh_token") or session.get("openai_rt") or "").strip()
        if refresh_token:
            db.event(
                f"[{email}] [反代] 当前账号未开放 Agent Identity，已使用现有 Refresh Token 回退到标准 sub2api OAuth 导入",
                "warning",
                detail={"email": email, "scope": "selected", "fallback": "oauth_refresh_token"},
            )
            data = _import_sub2api(db, email, account_id, session, proxy_url=proxy_url)
            if isinstance(data, dict):
                data = {**data, "_sunny_import_mode": "oauth_refresh_token"}
            return data
        raise AgentIdentityUnavailableError(
            f"{exc}；当前账号没有 Refresh Token，无法回退到标准 OAuth 导入。"
            "请改用“Codex 接码绑定”获取 Refresh Token 后，再执行“导入反代平台”"
        ) from exc
    except Exception as exc:
        if _is_cancel_exception(exc):
            raise
        raise RuntimeError(f"Agent Identity 凭证创建失败: {exc}") from exc
    auth_json["notes"] = _sub2api_notes(db, email, session)
    auth_content = json.dumps(auth_json, ensure_ascii=False, separators=(",", ":"))
    payload = {
        "contents": [auth_content],
        "update_existing": True,
    }
    db.ensure_not_cancelled()
    endpoint = _sub2api_codex_import_url(base_url)
    headers = {
        "X-API-Key": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "SunnyRegister/1.0",
    }
    resp = _post_sub2api_agent_identity(db, endpoint, headers, payload, proxy_url=proxy_url)
    if resp.status_code in {400, 404, 422} and "content" in str(resp.text or "").lower():
        # Older Sub2API builds accepted a single content field. Only retry
        # schema-level rejections, so a successful import is never duplicated.
        legacy_payload = {**payload, "content": auth_content}
        legacy_payload.pop("contents", None)
        resp = _post_sub2api_agent_identity(db, endpoint, headers, legacy_payload, proxy_url=proxy_url)
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(
            f"sub2api Agent Identity 导入失败: {_sub2api_response_diagnostic(resp, endpoint)}"
        )
    response_json = _sub2api_response_json(resp, endpoint)
    result = response_json.get("data") if isinstance(response_json, dict) and isinstance(response_json.get("data"), dict) else response_json
    if not isinstance(result, dict):
        raise RuntimeError("sub2api Agent Identity 导入结果格式无效")
    failed = int(result.get("failed") or 0)
    created = int(result.get("created") or 0)
    updated = int(result.get("updated") or 0)
    if failed > 0 or (created + updated <= 0 and int(result.get("skipped") or 0) <= 0):
        errors = result.get("errors") or result.get("items") or []
        raise RuntimeError(f"sub2api Agent Identity 导入未成功: {json.dumps(errors, ensure_ascii=False)[:500]}")
    remote_id = ""
    items = result.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        remote_id = str(items[0].get("account_id") or "")
    db.set_account_sub2api_status(email, "imported", remote_id)
    db.event(
        f"[{email}] [反代] 已使用 Agent Identity auth.json 导入 sub2api，后续请求由平台动态签名",
        detail={"email": email, "scope": "selected", "account_id": account_id, "auth_mode": "agentIdentity"},
    )
    return result


def _post_sub2api_agent_identity(
    db: SunnyDB,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    proxy_url: str = "",
):
    """Post one import payload, retrying only transient gateway failures."""
    response = None
    for attempt in range(3):
        db.ensure_not_cancelled()
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=90,
                allow_redirects=False,
                proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
            )
        except requests.RequestException as exc:
            if attempt >= 2:
                raise RuntimeError(f"sub2api Agent Identity 导入请求失败: {exc}") from exc
            db.event(
                f"[反代] sub2api 导入请求异常，准备重试 {attempt + 1}/2",
                "warning",
                detail={"scope": "global", "attempt": attempt + 1},
            )
            time.sleep(1.5 * (attempt + 1))
            continue
        status = int(getattr(response, "status_code", 0) or 0)
        if status not in {429, 502, 503, 504} or attempt >= 2:
            return response
        db.event(
            f"[反代] sub2api 网关暂时不可用，准备重试 {attempt + 1}/2（HTTP {status}）",
            "warning",
            detail={"scope": "global", "attempt": attempt + 1, "status": status},
        )
        time.sleep(1.5 * (attempt + 1))
    if response is None:
        raise RuntimeError("sub2api Agent Identity 导入请求未返回响应")
    return response


def _sub2api_response_diagnostic(response: Any, endpoint: str) -> str:
    status = int(getattr(response, "status_code", 0) or 0)
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("Content-Type") or headers.get("content-type") or "unknown")
    location = str(headers.get("Location") or headers.get("location") or "").strip()
    final_url = str(getattr(response, "url", "") or endpoint)
    body = str(getattr(response, "text", "") or "").strip()
    lowered = body.lower()
    if 300 <= status < 400:
        target = location or "未提供 Location"
        return f"HTTP {status}，接口发生重定向到 {target}；请检查 Base URL、Cloudflare 与鉴权配置"
    if "<html" in lowered or "<!doctype" in lowered:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "HTML 页面"
        return (
            f"HTTP {status}，服务返回 HTML（{title}，Content-Type={content_type}，URL={final_url}）；"
            "请检查 sub2api API 路径、Cloudflare 回源状态及 Admin Token"
        )
    summary = re.sub(r"\s+", " ", body)[:500] or "空响应"
    return f"HTTP {status}，Content-Type={content_type}，URL={final_url}，响应={summary}"


def _sub2api_response_json(response: Any, endpoint: str) -> dict[str, Any]:
    try:
        value = response.json()
    except Exception:
        body = str(getattr(response, "text", "") or "").strip()
        try:
            value = json.loads(body)
        except Exception as exc:
            raise RuntimeError(
                f"sub2api Agent Identity 导入返回非 JSON 内容: {_sub2api_response_diagnostic(response, endpoint)}"
            ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("sub2api Agent Identity 导入结果必须是 JSON 对象")
    return value


def _sub2api_codex_import_url(base_url: str) -> str:
    cleaned = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("sub2api Base URL 必须是完整的 http:// 或 https:// 地址")
    endpoint = "/api/v1/admin/accounts/import/codex-session"
    if cleaned.endswith(endpoint):
        return cleaned
    for suffix in ("/api/v1/admin", "/api/v1"):
        if cleaned.endswith(suffix):
            return cleaned[: -len(suffix)] + endpoint
    return cleaned + endpoint


def _persist_registration_checkpoint(
    db: SunnyDB,
    mailbox: dict[str, Any],
    account,
    checkpoint: str,
    snapshot: dict[str, Any],
    original_status: str,
) -> None:
    mailbox_id = max(0, int(mailbox.get("id") or 0))
    email = str(mailbox.get("email") or account.email or "")
    if mailbox_id <= 0 or not email:
        return
    candidate = "已接码" if checkpoint == "phone_bound" else "已注册"
    current_status = db.mailbox_status(mailbox_id)
    completed_status = _highest_mailbox_progress(
        _highest_mailbox_progress(original_status, current_status),
        candidate,
    )
    refresh_token = str(snapshot.get("refresh_token") or snapshot.get("openai_rt") or "").strip()
    access_token = str(snapshot.get("access_token") or "").strip()
    fields: dict[str, Any] = {
        "mailbox_id": mailbox_id,
        "status": _account_status_for_mailbox(completed_status),
        "account_type": snapshot.get("plan_type") or account.account_type,
        "last_error": "",
        "metadata_json": json.dumps(
            {"task_id": db.task_id, "source": "sunny_register", "checkpoint": checkpoint, "completed_status": completed_status},
            ensure_ascii=False,
        ),
    }
    if refresh_token:
        fields["openai_rt"] = refresh_token
    if access_token:
        fields["access_token"] = access_token
    if snapshot.get("phone_number"):
        fields["phone_number"] = str(snapshot.get("phone_number") or "")
    account_id = db.upsert_account(email, **fields)
    if access_token or snapshot.get("session_json"):
        db.upsert_session(email, account_id, snapshot, account.raw)
    db.mark_mailbox(mailbox_id, completed_status, openai_rt=refresh_token)
    db.event(
        f"[{email}] [系统] 已保存任务阶段检查点：{completed_status}",
        detail={"email": email, "scope": "selected", "checkpoint": checkpoint, "completed_status": completed_status},
    )


def _run_one(
    db: SunnyDB,
    task_type: str,
    payload: dict[str, Any],
    mailbox: dict[str, Any],
    index: int,
    total: int,
    protocol_batch_policy: _ProtocolBatchPolicy | None = None,
) -> tuple[bool, dict[str, Any] | str]:
    db.ensure_not_cancelled()
    email = mailbox.get("email") or f"mailbox-{index}"
    stage = _stage(payload)
    setup_login_secret_enabled = payload.get("setup_login_secret") is True
    explicit_rt_acquire = task_type == "sunny_acquire_rt"
    _emit_registration_progress(db, str(email), stage, "initializing", setup_login_secret=setup_login_secret_enabled)
    try:
        proxies = _prepare_register_proxy(db, payload, str(email), index - 1)
    except Exception as exc:
        if _is_cancel_exception(exc):
            raise
        mailbox_id = max(0, int(mailbox.get("id") or 0))
        err_text = str(exc)
        err = f"[{email}] {err_text}"
        mailbox_status = str(mailbox.get("status") or "")
        completed_status = mailbox_status if _MAILBOX_PROGRESS_RANK.get(mailbox_status, -1) > 0 else ""
        if completed_status:
            db.mark_mailbox(mailbox_id, completed_status, err_text)
            db.upsert_account(str(email), mailbox_id=mailbox_id, status=_account_status_for_mailbox(completed_status), last_error=err_text)
        else:
            db.mark_mailbox(mailbox_id, "失败", err_text)
            db.upsert_account(str(email), mailbox_id=mailbox_id, status="failed", last_error=err_text)
        db.event(
            err,
            "error",
            detail={"email": email, "scope": "selected", "proxy_pool_exhausted": True, "traceback": traceback.format_exc()[-3000:]},
        )
        _emit_registration_progress(db, str(email), stage, "failed", state="abnormal", error=err_text, setup_login_secret=setup_login_secret_enabled)
        return False, err
    auxiliary_proxy = _auxiliary_proxy(payload, proxies)
    chatgpt_proxy_label = redact_proxy_url(str(proxies.get("register") or ""))
    auxiliary_proxy_label = redact_proxy_url(auxiliary_proxy)
    db.event(
        f"[{email}] [代理] ChatGPT 官方流量使用{chatgpt_proxy_label or '系统直连'}；其他流程使用{auxiliary_proxy_label or '系统直连'}",
        detail={"email": email, "scope": "selected", "chatgpt_proxy": chatgpt_proxy_label, "auxiliary_proxy": auxiliary_proxy_label, "proxy_all_traffic": payload.get("proxy_all_traffic") is True},
    )
    execution_mode = str(payload.get("execution_mode") or payload.get("mode") or "background").strip().lower()
    if execution_mode not in {"background", "visible", "protocol"}:
        execution_mode = "background"
    headless = execution_mode == "background"
    protocol_challenge_strategy = str(payload.get("protocol_challenge_strategy") or "native_headless").strip().lower()
    if protocol_challenge_strategy not in {"native_headless", "sentinel_protocol"}:
        protocol_challenge_strategy = "native_headless"
    account = account_from_row(mailbox)
    mailbox_proxy_url = _mailbox_proxy_for_task(payload, proxies, auxiliary_proxy, account.mailbox_type)
    if mailbox_proxy_url and not auxiliary_proxy:
        db.event(
            f"[{email}] [邮箱] AT续期的 iCloud 邮箱 API 将复用当前认证代理，避免服务器直连不可达",
            detail={"email": email, "scope": "selected", "mailbox_proxy": redact_proxy_url(mailbox_proxy_url), "renewal_mailbox_proxy_fallback": True},
        )
    mailbox_id = max(0, int(mailbox.get("id") or 0))
    is_registered_mailbox = bool(account.openai_rt) or str(mailbox.get("status") or "") in {"registered", "已注册", "phone_bound", "已接码", "已反代", "reverse_proxied", "登录刷新"}
    traffic_meter = ProxyTrafficMeter(
        proxy_url=str(proxies.get("register") or ""),
        tracked_proxy=str(proxies.get("mode") or "") == "proxy_pool",
        email=str(email),
        operation=task_type,
    )
    traffic_scope = use_traffic_meter(traffic_meter)
    traffic_scope.__enter__()
    traffic_finished = False

    def finalize_traffic(registration_succeeded: bool) -> dict[str, Any]:
        nonlocal traffic_finished
        if traffic_finished:
            return traffic_meter.snapshot()
        traffic_finished = True
        snapshot = traffic_meter.snapshot()
        try:
            db.record_proxy_traffic(
                str(email),
                mailbox_id,
                int(snapshot.get("total_bytes") or 0),
                registration_attempt=task_type == "sunny_register" and not is_registered_mailbox,
                registration_succeeded=registration_succeeded,
            )
            host_summary = ", ".join(
                f"{host}={int((details or {}).get('bytes') or 0)}"
                for host, details in list((snapshot.get("by_host") or {}).items())[:3]
            )
            db.event(
                f"[{email}] [流量] 本次代理池 HTTP 应用层流量估算 {snapshot.get('total_bytes', 0)} bytes"
                f"（缓存回放已排除，不含 TLS/TCP 开销）"
                + (f"；主要域名 {host_summary}" if host_summary else ""),
                detail={"email": email, "scope": "selected", "proxy_traffic": snapshot},
            )
        except Exception as exc:
            db.event(f"[{email}] [流量] 保存代理池流量统计失败，已保留任务结果: {exc}", "warning", detail={"email": email, "scope": "selected"})
        finally:
            traffic_scope.__exit__(None, None, None)
        return snapshot
    original_mailbox_status = str(mailbox.get("status") or ("已注册" if is_registered_mailbox else "未注册"))
    original_completed_status = original_mailbox_status if _MAILBOX_PROGRESS_RANK.get(original_mailbox_status, -1) > 0 else ""
    db.upsert_account(
        str(email),
        mailbox_id=mailbox_id,
        status=_account_status_for_mailbox(original_completed_status) if original_completed_status else "pending",
        metadata_json=json.dumps(
            {
                "task_id": db.task_id,
                "source": "sunny_register",
                "checkpoint": "task_started",
                "completed_status": original_completed_status,
            },
            ensure_ascii=False,
        ),
    )
    db.event(f"[{email}] [系统] 开始注册/登录 {index}/{total}，阶段={_stage_label(stage)}", detail={"email": email, "scope": "selected", "stage": stage})
    if execution_mode == "protocol":
        mode_label = (
            "协议注册（Sentinel 协议运行时，仅证明生成使用窄范围 Camoufox）"
            if protocol_challenge_strategy == "sentinel_protocol"
            else "协议注册（本项目原生后台浏览器挑战接管）"
        )
    elif headless:
        mode_label = "后台浏览器自动（Camoufox Headless，无窗口）"
    else:
        mode_label = "可视浏览器自动（Chromium Visible，有窗口）"
    db.event(f"[{email}] [认证] 执行方式：{mode_label}", detail={"email": email, "scope": "selected", "execution_mode": execution_mode, "headless": headless})
    if proxies.get("register"):
        proxy_label = redact_proxy_url(proxies["register"])
        if proxies.get("mode") == "system_proxy":
            db.event(f"[{email}] [代理] 注册/登录流量使用服务器系统出口代理: {proxy_label}", detail={"email": email, "scope": "selected", "proxy": proxy_label, "proxy_mode": "system_proxy"})
        elif proxies.get("mode") == "local_proxy_fallback":
            db.event(f"[{email}] [代理] 注册/登录流量已切换为本地代理链路: {proxy_label}", detail={"email": email, "scope": "selected", "proxy": proxy_label, "proxy_mode": "local_proxy_fallback"})
        else:
            db.event(f"[{email}] [代理] 注册/登录流量使用代理池代理: {proxy_label}（代理池检测为轻量 TCP 连通检测，不等同于目标站点可访问）", detail={"email": email, "scope": "selected", "proxy": proxy_label, "proxy_mode": "proxy_pool"})
    else:
        db.event(f"[{email}] [代理] 注册/登录流量使用服务器系统网络直连出口", detail={"email": email, "scope": "selected", "proxy": "", "proxy_mode": "direct"})
    _emit_registration_progress(db, str(email), stage, "proxy_ready", setup_login_secret=setup_login_secret_enabled)
    db.mark_mailbox(mailbox_id, "登录刷新" if is_registered_mailbox else "注册中")

    def save_progress(checkpoint: str, snapshot: dict[str, Any]) -> None:
        _emit_registration_progress(db, str(email), stage, checkpoint, setup_login_secret=setup_login_secret_enabled)
        if checkpoint in {"registered", "phone_bound"}:
            _persist_registration_checkpoint(
                db,
                mailbox,
                account,
                checkpoint,
                snapshot,
                original_mailbox_status,
            )

    def setup_login_secret_in_browser(context, page, base_session: dict[str, Any]) -> dict[str, Any]:
        """Run optional LS setup inside the registration browser session.

        The browser flow owns this context and keeps its fingerprint/cookies
        alive until this callback returns; no second Camoufox instance is
        created for a freshly registered account.
        """
        if not setup_login_secret_enabled:
            return {}
        db.event(
            f"[{email}] [登录密钥] 在当前注册/登录浏览器中补充缺失的 ChatGPT 密码与 2FA",
            detail={"email": email, "scope": "selected", "setup_login_secret": True, "browser_reused": True},
        )
        return setup_login_secret(
            account,
            base_session,
            proxies["register"],
            lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
            should_cancel=db.cancel_requested,
            mailbox_proxy_url=mailbox_proxy_url,
            traffic_meter=traffic_meter,
            recent_email_code=str(base_session.get("recent_email_code") or ""),
            recent_email_code_at=float(base_session.get("recent_email_code_at") or 0.0),
            browser_page=page,
            browser_context=context,
            on_progress=lambda checkpoint: _emit_registration_progress(
                db, str(email), stage, checkpoint, setup_login_secret=True,
            ),
        )

    def setup_login_secret_in_protocol(protocol_client, base_session: dict[str, Any]) -> dict[str, Any]:
        """Run LS setup through the protocol registration cookie jar."""
        if not setup_login_secret_enabled:
            return {}
        db.event(
            f"[{email}] [登录密钥] 在当前协议登录态中补充缺失的 ChatGPT 密码与 2FA",
            detail={"email": email, "scope": "selected", "setup_login_secret": True, "protocol_session_reused": True},
        )
        return setup_login_secret_protocol(
            account,
            base_session,
            protocol_client,
            lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
            should_cancel=db.cancel_requested,
            mailbox_proxy_url=mailbox_proxy_url,
            recent_email_code=str(base_session.get("recent_email_code") or ""),
            recent_email_code_at=float(base_session.get("recent_email_code_at") or 0.0),
            on_progress=lambda checkpoint: _emit_registration_progress(
                db, str(email), stage, checkpoint, setup_login_secret=True,
            ),
        )

    wants_rt = stage in {CODEX_PHONE_BIND, IMPORT_REVERSE_PROXY} or explicit_rt_acquire
    phone_provider = None
    require_refresh_token = False
    phone_skipped_reason = ""
    if wants_rt:
        sms_cfg = db.get_config("phone")
        db.event(
            f"[{email}] [接码] 接码资源检查：自建号池可用 {db.usable_phone_count()} 个，LubanSMS={'启用' if _provider_is_available(db, 'luban') else '不可用'}，SMSBower={'启用' if _provider_is_available(db, 'smsbower') else '不可用'}，SMSPool={'启用' if _provider_is_available(db, 'smspool') else '不可用'}，FireFox={'启用' if _provider_is_available(db, 'firefox') else '不可用'}",
            detail={"email": email, "scope": "selected", "sms_provider": "resource_check", "phone_config": {"pool_enabled": sms_cfg.get("pool_enabled"), "luban_enabled": sms_cfg.get("luban_enabled"), "smsbower_enabled": sms_cfg.get("smsbower_enabled"), "smspool_enabled": sms_cfg.get("smspool_enabled"), "firefox_enabled": sms_cfg.get("firefox_enabled")}},
        )
        if account.openai_rt:
            require_refresh_token = True
            db.event(f"[{email}] [接码] 邮箱记录已有 OpenAI RT，将直接刷新 Session", detail={"email": email, "scope": "selected"})
        else:
            phone_provider = _combined_phone_provider(db, email, auxiliary_proxy, execution_mode)
        if phone_provider:
            require_refresh_token = True
            db.event(f"[{email}] [接码] 已启用组合接码策略：外部供应商随机尝试，自建手机号池作为兜底", detail={"email": email, "scope": "selected", "sms_provider": "combined"})
        elif explicit_rt_acquire:
            require_refresh_token = True
            db.event(
                f"[{email}] [Session] 账户没有已保存 RT，将通过已有账户登录态发起 Codex OAuth 授权；若上游要求手机号验证，则联动当前接码配置",
                detail={"email": email, "scope": "selected", "explicit_rt_acquire": True},
            )
        elif not account.openai_rt:
            phone_skipped_reason = "无可用手机号：自建手机号池无可用号码，且 LubanSMS/SMSBower/SMSPool/FireFox 均未启用或未完成配置。本账号只执行 ChatGPT 注册/登录，不进行接码，也不会获取 Refresh Token。"
            db.event(f"[{email}] [接码] {phone_skipped_reason}", "warning", detail={"email": email, "scope": "selected"})
    elif stage == AGENT_IDENTITY_REVERSE_PROXY:
        db.event(
            f"[{email}] [接码] 当前任务选择 Agent Identity 导入，将跳过手机号绑定并使用 Access Token 生成动态签名凭证",
            detail={"email": email, "scope": "selected", "stage": stage},
        )
    else:
        db.event(
            f"[{email}] [接码] 当前任务阶段为“仅注册 ChatGPT”，不会调用接码供应商，也不会获取 Refresh Token",
            detail={"email": email, "scope": "selected", "stage": stage},
        )

    try:
        db.ensure_not_cancelled()
        use_protocol_browser_fast_path = (
            execution_mode == "protocol"
            and protocol_challenge_strategy == "native_headless"
            and task_type == "sunny_register"
            and not is_registered_mailbox
            and protocol_batch_policy is not None
            and protocol_batch_policy.should_start_in_browser()
        )
        if use_protocol_browser_fast_path:
            db.event(
                f"[{email}] [认证] 本批次协议请求已连续触发浏览器挑战，直接启动后台无头接管以跳过重复协议验证",
                "warning",
                detail={
                    "email": email,
                    "scope": "selected",
                    "execution_mode": "protocol_headless_fast_path",
                    "protocol_challenge_strategy": protocol_challenge_strategy,
                },
            )
            session = login_or_register(
                account,
                proxies["register"],
                True,
                lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                phone_provider=phone_provider,
                existing_account=False,
                require_refresh_token=require_refresh_token,
                should_cancel=db.cancel_requested,
                execution_mode="protocol_headless_fallback",
                on_progress=save_progress,
                mailbox_proxy_url=mailbox_proxy_url,
                traffic_meter=traffic_meter,
                traffic_config=payload.get("browser_traffic_optimization"),
                post_registration_callback=setup_login_secret_in_browser if setup_login_secret_enabled else None,
            )
            session["requested_execution_mode"] = "protocol"
            session["execution_mode"] = "protocol_headless_fallback"
            session["protocol_fallback"] = "batch_challenge_fast_path"
        elif execution_mode == "protocol":
            try:
                session = login_or_register_protocol(
                    account,
                    proxies["register"],
                    lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                    existing_account=is_registered_mailbox or task_type == "sunny_login",
                    should_cancel=db.cancel_requested,
                    on_progress=save_progress,
                    challenge_strategy=protocol_challenge_strategy,
                    mailbox_proxy_url=mailbox_proxy_url,
                    traffic_meter=traffic_meter,
                    post_registration_callback=setup_login_secret_in_protocol if setup_login_secret_enabled else None,
                )
            except (ProtocolChallengeRequired, ProtocolRegistrationError) as protocol_error:
                is_challenge = isinstance(protocol_error, ProtocolChallengeRequired)
                retryable_transport_error = _is_retryable_protocol_transport_error(protocol_error)
                if not is_challenge and not (
                    protocol_challenge_strategy == "native_headless" and retryable_transport_error
                ):
                    raise
                if protocol_batch_policy is not None and protocol_challenge_strategy == "native_headless" and is_challenge:
                    protocol_batch_policy.record_challenge()
                db.ensure_not_cancelled()
                protocol_traffic = getattr(protocol_error, "traffic", None)
                if protocol_challenge_strategy == "sentinel_protocol":
                    db.event(
                        f"[{email}] [认证] Sentinel 协议运行时未能生成有效证明，任务不会切换到完整浏览器接管: {protocol_error}",
                        "error",
                        detail={
                            "email": email,
                            "scope": "selected",
                            "execution_mode": "protocol",
                            "protocol_challenge_strategy": protocol_challenge_strategy,
                            "protocol_traffic": protocol_traffic if isinstance(protocol_traffic, dict) else {},
                        },
                    )
                    raise
                fallback_reason = "浏览器挑战" if is_challenge else "可恢复的网络传输错误"
                db.event(
                    f"[{email}] [认证] 协议模式遇到{fallback_reason}，切换到后台无头浏览器继续注册/登录",
                    "warning",
                    detail={
                        "email": email,
                        "scope": "selected",
                        "execution_mode": "protocol_headless_fallback",
                        "protocol_error": str(protocol_error),
                        "protocol_traffic": protocol_traffic if isinstance(protocol_traffic, dict) else {},
                    },
                )
                session = login_or_register(
                    account,
                    proxies["register"],
                    True,
                    lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                    phone_provider=phone_provider,
                    existing_account=is_registered_mailbox or task_type == "sunny_login",
                    require_refresh_token=require_refresh_token,
                    should_cancel=db.cancel_requested,
                    execution_mode="protocol_headless_fallback",
                    on_progress=save_progress,
                    mailbox_proxy_url=mailbox_proxy_url,
                    traffic_meter=traffic_meter,
                    traffic_config=payload.get("browser_traffic_optimization"),
                    post_registration_callback=setup_login_secret_in_browser if setup_login_secret_enabled else None,
                )
                session["requested_execution_mode"] = "protocol"
                session["execution_mode"] = "protocol_headless_fallback"
                session["protocol_fallback"] = "headless"
                if isinstance(protocol_traffic, dict):
                    session["protocol_traffic"] = protocol_traffic
                db.event(
                    f"[{email}] [认证] 协议模式的后台无头浏览器接管已完成",
                    detail={
                        "email": email,
                        "scope": "selected",
                        "execution_mode": "protocol_headless_fallback",
                    },
                )
            else:
                if protocol_batch_policy is not None:
                    protocol_batch_policy.record_success()
                protocol_session = session
                if wants_rt and require_refresh_token:
                    db.event(
                        f"[{email}] [认证] 协议注册/登录已完成，复用当前登录态进入后台 OAuth 续段以完成接码和 Refresh Token 获取",
                        detail={"email": email, "scope": "selected", "execution_mode": "protocol_post_stage"},
                    )
                    try:
                        session = login_or_register(
                            account,
                            proxies["register"],
                            True,
                            lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                            phone_provider=phone_provider,
                            existing_account=True,
                            require_refresh_token=True,
                            should_cancel=db.cancel_requested,
                            execution_mode="protocol_post_stage",
                            on_progress=save_progress,
                            mailbox_proxy_url=mailbox_proxy_url,
                            existing_session=protocol_session,
                            traffic_meter=traffic_meter,
                            traffic_config=payload.get("browser_traffic_optimization"),
                            post_registration_callback=setup_login_secret_in_browser if setup_login_secret_enabled else None,
                        )
                        session["requested_execution_mode"] = "protocol"
                        session["execution_mode"] = "protocol_post_stage"
                        if isinstance(protocol_session.get("protocol_traffic"), dict):
                            session["protocol_traffic"] = protocol_session["protocol_traffic"]
                    except Exception as exc:
                        if _is_cancel_exception(exc):
                            raise
                        session = protocol_session
                        session["post_registration_error"] = f"协议注册已完成，但后续接码/OAuth 阶段失败: {exc}"
                        db.event(
                            f"[{email}] [接码] 协议注册已完成，后续接码/OAuth 阶段失败，账号保留为已注册: {exc}",
                            "warning",
                            detail={"email": email, "scope": "selected", "execution_mode": "protocol_post_stage"},
                        )
        else:
            session = login_or_register(
                account,
                proxies["register"],
                headless,
                lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                phone_provider=phone_provider,
                existing_account=is_registered_mailbox or task_type == "sunny_login",
                require_refresh_token=require_refresh_token,
                should_cancel=db.cancel_requested,
                execution_mode=execution_mode,
                on_progress=save_progress,
                mailbox_proxy_url=mailbox_proxy_url,
                traffic_meter=traffic_meter,
                traffic_config=payload.get("browser_traffic_optimization"),
                post_registration_callback=setup_login_secret_in_browser if setup_login_secret_enabled else None,
            )
        db.ensure_not_cancelled()
        generated_password = str(session.pop("generated_chatgpt_password", "") or "")
        if generated_password:
            db.save_chatgpt_password(mailbox_id, generated_password)
            account.chatgpt_password = generated_password
            db.event(
                f"[{email}] [认证] 已保存本次注册生成的 ChatGPT 密码",
                detail={"email": email, "scope": "selected", "credential": "chatgpt_password"},
            )
        login_secret_result: dict[str, Any] | None = session.pop("login_secret_result", None)
        login_secret_from_browser = login_secret_result is not None
        recent_email_code = str(session.get("recent_email_code") or "").strip()
        try:
            recent_email_code_at = float(session.get("recent_email_code_at") or 0.0)
        except (TypeError, ValueError):
            recent_email_code_at = 0.0
        session.pop("recent_email_code", None)
        session.pop("recent_email_code_at", None)
        if (
            login_secret_result is not None
            and login_secret_result.get("browser_challenge_required") is True
            and execution_mode == "protocol"
            and protocol_challenge_strategy == "native_headless"
        ):
            protocol_login_secret_result = login_secret_result
            if isinstance(protocol_login_secret_result.get("session"), dict):
                session = protocol_login_secret_result["session"]
            db.event(
                f"[{email}] [登录密钥] 协议登录密钥流程遇到浏览器挑战，将携带当前协议 Cookie 登录态由 Camoufox 后台接管",
                "warning",
                detail={"email": email, "scope": "selected", "protocol_login_secret_browser_takeover": True},
            )
            try:
                browser_result = setup_login_secret(
                    account,
                    session,
                    proxies["register"],
                    lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                    should_cancel=db.cancel_requested,
                    mailbox_proxy_url=mailbox_proxy_url,
                    traffic_meter=traffic_meter,
                    recent_email_code=recent_email_code,
                    recent_email_code_at=recent_email_code_at,
                    force_access_token_refresh=True,
                    on_progress=lambda checkpoint: _emit_registration_progress(
                        db, str(email), stage, checkpoint, setup_login_secret=True,
                    ),
                )
                for key in ("password_added", "totp_added"):
                    if protocol_login_secret_result.get(key):
                        browser_result[key] = True
                for key in ("password", "totp_secret"):
                    if not browser_result.get(key) and protocol_login_secret_result.get(key):
                        browser_result[key] = protocol_login_secret_result[key]
                login_secret_result = browser_result
                login_secret_from_browser = True
                if isinstance(browser_result.get("session"), dict):
                    session = browser_result["session"]
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                errors = list(protocol_login_secret_result.get("errors") or [])
                errors.append(f"浏览器挑战接管失败: {exc}")
                login_secret_result = {**protocol_login_secret_result, "complete": False, "errors": errors}
        if payload.get("setup_login_secret") is True and login_secret_result is None:
            db.event(
                f"[{email}] [登录密钥] 开始补充缺失的 ChatGPT 密码与 2FA",
                detail={"email": email, "scope": "selected", "setup_login_secret": True},
            )
            try:
                login_secret_result = setup_login_secret(
                    account,
                    session,
                    proxies["register"],
                    lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                    should_cancel=db.cancel_requested,
                    mailbox_proxy_url=mailbox_proxy_url,
                    traffic_meter=traffic_meter,
                    recent_email_code=recent_email_code,
                    recent_email_code_at=recent_email_code_at,
                    on_progress=lambda checkpoint: _emit_registration_progress(
                        db, str(email), stage, checkpoint, setup_login_secret=True,
                    ),
                )
                if login_secret_result.get("password_added"):
                    db.save_chatgpt_password(mailbox_id, str(login_secret_result.get("password") or ""))
                if login_secret_result.get("totp_added"):
                    db.save_totp_secret(mailbox_id, str(login_secret_result.get("totp_secret") or ""))
                if isinstance(login_secret_result.get("session"), dict):
                    session = login_secret_result["session"]
                if login_secret_result.get("complete"):
                    db.event(
                        f"[{email}] [登录密钥] {_login_secret_result_message(login_secret_result)}",
                        detail={"email": email, "scope": "selected", "login_secret_complete": True},
                    )
                else:
                    db.event(
                        f"[{email}] [登录密钥] {_login_secret_result_message(login_secret_result)}",
                        "warning",
                        detail={
                            "email": email,
                            "scope": "selected",
                            "login_secret_complete": False,
                            "password_complete": bool(login_secret_result.get("password")),
                            "totp_complete": bool(login_secret_result.get("totp_secret")),
                            "access_token_refreshed": bool(login_secret_result.get("access_token_refreshed")),
                        },
                    )
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                login_secret_result = {"complete": False, "errors": [str(exc)]}
                db.event(f"[{email}] [登录密钥] 账户已注册，但添加密码与 2FA 失败: {exc}", "warning", detail={"email": email, "scope": "selected", "login_secret_complete": False})
        if login_secret_from_browser and login_secret_result is not None:
            if login_secret_result.get("password_added"):
                db.save_chatgpt_password(mailbox_id, str(login_secret_result.get("password") or ""))
            if login_secret_result.get("totp_added"):
                db.save_totp_secret(mailbox_id, str(login_secret_result.get("totp_secret") or ""))
            if isinstance(login_secret_result.get("session"), dict):
                session = login_secret_result["session"]
            if login_secret_result.get("complete"):
                db.event(
                    f"[{email}] [登录密钥] {_login_secret_result_message(login_secret_result)}",
                    detail={"email": email, "scope": "selected", "login_secret_complete": True},
                )
            else:
                db.event(
                    f"[{email}] [登录密钥] {_login_secret_result_message(login_secret_result)}",
                    "warning",
                    detail={
                        "email": email,
                        "scope": "selected",
                        "login_secret_complete": False,
                        "password_complete": bool(login_secret_result.get("password")),
                        "totp_complete": bool(login_secret_result.get("totp_secret")),
                        "access_token_refreshed": bool(login_secret_result.get("access_token_refreshed")),
                    },
                )
        if session.get("phone_binding_skipped_reason"):
            phone_skipped_reason = str(session.get("phone_binding_skipped_reason") or "")
        rt_value = session.get("refresh_token") or session.get("openai_rt") or account.openai_rt
        has_rt = bool(rt_value)
        phone_bound = bool(session.get("phone_bound")) or has_rt
        candidate_status = "已接码" if phone_bound else "已注册"
        mailbox_status = _highest_mailbox_progress(original_mailbox_status, candidate_status)
        account_id = db.upsert_account(
            email,
            mailbox_id=mailbox_id,
            status=_account_status_for_mailbox(mailbox_status),
            account_type=session.get("plan_type") or account.account_type,
            openai_rt=rt_value,
            access_token=session.get("access_token", ""),
            last_error="",
            metadata_json=json.dumps({"task_id": db.task_id, "source": "sunny_register", "stage": stage, "checkpoint": "flow_completed", "completed_status": mailbox_status, "phone_skipped_reason": phone_skipped_reason}, ensure_ascii=False),
        )
        db.upsert_session(email, account_id, session, account.raw)
        action = str(session.get("auth_action") or "login")
        action_label = "注册" if action == "register" else "登录"
        db.mark_mailbox(mailbox_id, mailbox_status, openai_rt=rt_value)
        post_registration_error = str(session.get("post_registration_error") or "").strip()
        result: dict[str, Any] = {
            "email": email,
            "account_id": account_id,
            "auth_action": action,
            "execution_mode": str(session.get("execution_mode") or execution_mode),
            "stage": stage,
            "access_token": session.get("access_token", ""),
            "refresh_token": rt_value,
            "has_session": bool(session.get("access_token")),
            "phone_bound": phone_bound,
            "completed_status": mailbox_status,
            "stage_complete": stage == REGISTER_ONLY or (stage == CODEX_PHONE_BIND and has_rt),
            "phone_skipped_reason": phone_skipped_reason,
        }
        base_stage_complete = bool(result["stage_complete"])
        if login_secret_result is not None:
            result["login_secret_complete"] = bool(login_secret_result.get("complete"))
            result["login_secret_errors"] = list(login_secret_result.get("errors") or [])
            if not result["login_secret_complete"]:
                login_secret_error = "；".join(result["login_secret_errors"] or ["密码与 2FA 未全部完成"])
                result["stage_error"] = "; ".join(filter(None, [str(result.get("stage_error") or ""), login_secret_error]))
        if isinstance(session.get("protocol_traffic"), dict):
            result["protocol_traffic"] = session["protocol_traffic"]
        if session.get("protocol_fallback"):
            result["protocol_fallback"] = str(session["protocol_fallback"])
        result["proxy_traffic"] = traffic_meter.snapshot()
        if post_registration_error:
            result["stage_error"] = post_registration_error
        db.event(f"[{email}] [认证] 识别为{action_label}成功，已保存 ChatGPT Session" + (" 和 Refresh Token" if result["refresh_token"] else ""), detail={"email": email, "scope": "selected", **result})
        if stage == IMPORT_REVERSE_PROXY:
            if not result["refresh_token"]:
                result["sub2api_skipped_reason"] = "没有 Refresh Token，已停止导入反代平台"
                result["stage_complete"] = False
                result.setdefault("stage_error", post_registration_error or result["sub2api_skipped_reason"])
                db.upsert_account(email, mailbox_id=mailbox_id, status=_account_status_for_mailbox(mailbox_status), last_error=result["stage_error"])
                db.mark_mailbox(mailbox_id, mailbox_status, result["stage_error"], openai_rt=rt_value)
                db.event(
                    f"[{email}] [反代] 没有 Refresh Token，已停止导入 sub2api；OAuth 原因：{result['stage_error']}",
                    "warning",
                    detail={"email": email, "scope": "selected", "oauth_error": result["stage_error"]},
                )
            else:
                try:
                    _emit_registration_progress(db, str(email), stage, "reverse_importing", setup_login_secret=setup_login_secret_enabled)
                    result["sub2api"] = _import_sub2api(db, email, account_id, session, proxy_url=auxiliary_proxy)
                    mailbox_status = _highest_mailbox_progress(mailbox_status, "已反代")
                    db.mark_mailbox(mailbox_id, mailbox_status, openai_rt=rt_value)
                    db.upsert_account(email, mailbox_id=mailbox_id, status="reverse_proxied", last_error="")
                    result["completed_status"] = mailbox_status
                    result["stage_complete"] = True
                    _emit_registration_progress(db, str(email), stage, "reverse_imported", setup_login_secret=setup_login_secret_enabled)
                except Exception as exc:
                    stage_error = str(exc)
                    result["stage_complete"] = False
                    result["stage_error"] = stage_error
                    result["sub2api_error"] = stage_error
                    db.set_account_sub2api_status(email, "failed", error=stage_error)
                    db.mark_mailbox(mailbox_id, mailbox_status, stage_error, openai_rt=rt_value)
                    db.event(f"[{email}] [反代] 导入 sub2api 失败，账号保留为{mailbox_status}: {stage_error}", "error", detail={"email": email, "scope": "selected", "completed_status": mailbox_status})
        elif stage == AGENT_IDENTITY_REVERSE_PROXY:
            try:
                _emit_registration_progress(db, str(email), stage, "agent_identity_importing", setup_login_secret=setup_login_secret_enabled)
                import_result = _import_sub2api_agent_identity(
                    db,
                    email,
                    account_id,
                    session,
                    auxiliary_proxy,
                )
                import_mode = str(import_result.pop("_sunny_import_mode", "agent_identity")) if isinstance(import_result, dict) else "agent_identity"
                result["sub2api"] = import_result
                mailbox_status = _highest_mailbox_progress(mailbox_status, "已反代")
                db.mark_mailbox(mailbox_id, mailbox_status, openai_rt=rt_value)
                db.upsert_account(email, mailbox_id=mailbox_id, status="reverse_proxied", last_error="")
                result["completed_status"] = mailbox_status
                result["stage_complete"] = True
                result["agent_identity"] = import_mode == "agent_identity"
                result["agent_identity_fallback"] = import_mode != "agent_identity"
                _emit_registration_progress(db, str(email), stage, "agent_identity_imported", setup_login_secret=setup_login_secret_enabled)
            except Exception as exc:
                if _is_cancel_exception(exc):
                    raise
                stage_error = str(exc)
                result["stage_complete"] = False
                result["stage_error"] = stage_error
                result["sub2api_error"] = stage_error
                db.set_account_sub2api_status(email, "failed", error=stage_error)
                db.mark_mailbox(mailbox_id, mailbox_status, stage_error, openai_rt=rt_value)
                db.upsert_account(email, mailbox_id=mailbox_id, status=_account_status_for_mailbox(mailbox_status), last_error=stage_error)
                db.event(
                    f"[{email}] [反代] 绕过接码导入反代平台未完成，账号保留为{mailbox_status}: {stage_error}",
                    "error",
                    detail={"email": email, "scope": "selected", "completed_status": mailbox_status},
                )
        elif wants_rt and not result["stage_complete"]:
            stage_error = post_registration_error or phone_skipped_reason or "接码/Refresh Token 阶段未完成"
            result["stage_error"] = stage_error
            db.upsert_account(email, mailbox_id=mailbox_id, status=_account_status_for_mailbox(mailbox_status), last_error=stage_error)
            db.mark_mailbox(mailbox_id, mailbox_status, stage_error, openai_rt=rt_value)
            db.event(f"[{email}] [接码] 后续接码阶段未完成，账号保留为{mailbox_status}: {stage_error}", "warning", detail={"email": email, "scope": "selected", "completed_status": mailbox_status})
        if login_secret_result is not None:
            # LS is an optional post-registration phase. Keep the account and its
            # base registration result, but mark the task progress partial when
            # either password or TOTP setup did not finish.
            base_stage_complete = bool(result.get("stage_complete"))
            result["stage_complete"] = bool(result.get("stage_complete") and login_secret_result.get("complete"))
        elif setup_login_secret_enabled:
            result["stage_complete"] = False
        result["has_access_token"] = bool(result.pop("access_token", ""))
        result["has_refresh_token"] = bool(result.pop("refresh_token", ""))
        terminal_checkpoint = {
            REGISTER_ONLY: "registered",
            CODEX_PHONE_BIND: "phone_bound",
            IMPORT_REVERSE_PROXY: "reverse_imported",
            AGENT_IDENTITY_REVERSE_PROXY: "agent_identity_imported",
        }.get(stage, "registered")
        terminal_checkpoint = (
            "login_secret_completed"
            if setup_login_secret_enabled and result.get("stage_complete")
            else "login_secret_failed"
            if setup_login_secret_enabled and base_stage_complete
            else terminal_checkpoint
        )
        _emit_registration_progress(
            db,
            str(email),
            stage,
            terminal_checkpoint
            if result.get("stage_complete") or (setup_login_secret_enabled and base_stage_complete)
            else "stage_incomplete",
            state="completed" if result.get("stage_complete") else "abnormal",
            error=str(result.get("stage_error") or ""),
            setup_login_secret=setup_login_secret_enabled,
        )
        result["proxy_traffic"] = finalize_traffic(True)
        return True, result
    except Exception as exc:
        if _is_cancel_exception(exc):
            finalize_traffic(False)
            current_status = db.mailbox_status(mailbox_id)
            completed_status = _highest_mailbox_progress(original_mailbox_status, current_status)
            if _MAILBOX_PROGRESS_RANK.get(completed_status, -1) > 0:
                db.mark_mailbox(mailbox_id, completed_status)
                db.event(f"[{email}] [系统] 用户已停止任务，账号保留在上一个完成状态：{completed_status}", "warning", detail={"email": email, "scope": "selected", "cancelled": True, "completed_status": completed_status})
            else:
                db.mark_mailbox(mailbox_id, "失败", "任务已由用户停止，当前邮箱尚未完成 ChatGPT 注册")
                db.event(f"[{email}] [系统] 用户已停止任务，当前邮箱尚未完成 ChatGPT 注册并已标记为失败", "warning", detail={"email": email, "scope": "selected", "cancelled": True})
            raise
        err_text = str(exc)
        err = f"[{email}] {err_text}"
        traffic_snapshot = finalize_traffic(False)
        traffic = getattr(exc, "traffic", None)
        if _is_account_deactivated(err_text):
            db.mark_account_deactivated(email, err_text)
            db.event(
                f"[{email}] [认证] OpenAI 返回 account_deactivated，账户已标记为已封禁",
                "warning",
                detail={"email": email, "scope": "selected", "account_deactivated": True},
            )
        elif "Phone verification required" in err_text or "phone verification" in err_text.lower():
            db.mark_mailbox(mailbox_id, "需二验", err_text)
            db.event(f"[{email}] [接码] 账号需要手机号二次验证，但当前没有可用接码配置，本账号流程已停止", "warning", detail={"email": email, "scope": "selected"})
        elif original_completed_status:
            db.mark_mailbox(mailbox_id, original_completed_status, err_text)
            db.upsert_account(email, mailbox_id=mailbox_id, status=_account_status_for_mailbox(original_completed_status), last_error=err_text)
            db.event(f"[{email}] [系统] 后续操作失败，账号保留在已完成状态：{original_completed_status}", "warning", detail={"email": email, "scope": "selected", "completed_status": original_completed_status})
        else:
            db.mark_mailbox(mailbox_id, "失败", err_text)
            db.upsert_account(email, mailbox_id=mailbox_id, status="failed", last_error=err_text)
        error_detail = {"email": email, "scope": "selected", "traceback": traceback.format_exc()[-3000:]}
        if isinstance(traffic, dict):
            error_detail["protocol_traffic"] = traffic
        error_detail["proxy_traffic"] = traffic_snapshot
        db.event(err, "error", detail=error_detail)
        _emit_registration_progress(db, str(email), stage, "failed", state="abnormal", error=err_text, setup_login_secret=setup_login_secret_enabled)
        return False, err


def _run_one_isolated(
    task_id: str,
    task_type: str,
    payload: dict[str, Any],
    mailbox: dict[str, Any],
    index: int,
    total: int,
    protocol_batch_policy: _ProtocolBatchPolicy | None = None,
) -> tuple[int, bool, dict[str, Any] | str]:
    """Run one mailbox in its own DB connection/thread.

    Each browser flow owns exactly one mailbox/account object, one Outlook reader,
    one browser context and one SQLite connection. This keeps concurrent OTP reads
    and mailbox state updates isolated from other mailboxes.
    """
    worker_db = SunnyDB(task_id, ensure_schema=False)
    try:
        ok, result = _run_one(worker_db, task_type, payload, mailbox, index, total, protocol_batch_policy)
        return index, ok, result
    finally:
        worker_db.close()


def _refresh_sessions_sequential(db: SunnyDB, payload: dict[str, Any]) -> tuple[int, list[str], list[dict[str, Any]]]:
    accounts = db.fetch_accounts(_ids(payload.get("account_ids")) or None)
    index_offset = max(0, int(payload.get("_renewal_index_offset") or 0))
    total_accounts = max(1, int(payload.get("_renewal_total") or len(accounts) or 1))
    parallel = bool(payload.get("_renewal_parallel"))
    ok = 0
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for idx, acc in enumerate(accounts, start=index_offset + 1):
        db.ensure_not_cancelled()
        email = acc.get("email") or ""
        renewal_current = 1
        renewal_total = 7
        _emit_renewal_progress(db, email, renewal_current, renewal_total, "preparing")
        try:
            mailbox = db.fetch_mailbox_by_email(email)
            rt = acc.get("openai_rt") or ""
            if not rt:
                sess = db.fetch_session_by_email(email) or {}
                rt = sess.get("refresh_token") or ""
            renewal_current = 2
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "credentials_loaded")
            refresh_error = ""
            if rt:
                try:
                    renewal_current = 3
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "refresh_token_ready")
                    token = refresh_openai_access_token(rt, _proxy_snapshot(payload, idx - 1)["register"])
                    db.ensure_not_cancelled()
                    renewal_current = 4
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "token_received")
                    account_id = int(acc.get("id") or db.upsert_account(email))
                    payload2 = {"access_token": token.get("access_token"), "refresh_token": token.get("refresh_token") or rt, "id_token": token.get("id_token", ""), "expires_at": token.get("expires_at"), "session_json": token}
                    renewal_current = 5
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "saving_session")
                    db.upsert_session(email, account_id, payload2)
                    refreshed_status = "已接码" if payload2["refresh_token"] else "已注册"
                    current_status = str((mailbox or {}).get("status") or acc.get("status") or "")
                    completed_status = _highest_mailbox_progress(current_status, refreshed_status)
                    db.upsert_account(email, status=_account_status_for_mailbox(completed_status), access_token=payload2["access_token"], openai_rt=payload2["refresh_token"])
                    db.mark_mailbox_by_email(email, completed_status, openai_rt=payload2["refresh_token"])
                    renewal_current = 6
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "session_saved")
                    items.append({"email": email, "has_access_token": bool(payload2["access_token"]), "has_refresh_token": bool(payload2["refresh_token"]), "refresh_method": "refresh_token"})
                    ok += 1
                    _account_event(db, email, "session", "access_token.renewed", f"[{email}] [Session] 已通过 Refresh Token 完成 AT 续期", account_id=account_id)
                    renewal_current = 7
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "completed", state="succeeded")
                    if not parallel:
                        db.update_task(progress_current=idx, success_count=ok, error_count=len(errors))
                    continue
                except Exception as exc:
                    if _is_cancel_exception(exc):
                        raise
                    if _is_account_deactivated(exc):
                        raise
                    refresh_error = str(exc)
                    renewal_total = 9
                    renewal_current = 3
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "refresh_token_unavailable")
                    _account_event(db, email, "session", "refresh_token.unavailable", f"[{email}] [Session] Refresh Token 续期不可用，改用后台无头登录更新 AT：{refresh_error}", "warning", account_id=int(acc.get("id") or 0))
            else:
                renewal_total = 9
                renewal_current = 3
                _emit_renewal_progress(db, email, renewal_current, renewal_total, "refresh_token_missing")
                _account_event(db, email, "session", "refresh_token.missing", f"[{email}] [Session] 账户没有可用 Refresh Token，改用后台无头登录更新 AT", "warning", account_id=int(acc.get("id") or 0))

            if not mailbox:
                raise RuntimeError("找不到该账户对应的邮箱凭证，无法回退登录更新 AT")
            renewal_current = 4
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "mailbox_ready")
            fallback_payload = dict(payload)
            fallback_payload.update(
                {
                    "execution_mode": "protocol",
                    "protocol_challenge_strategy": "native_headless",
                    "registration_stage": "register_only",
                    "access_token_renewal": True,
                    "mailbox_ids": [int(mailbox.get("id") or 0)],
                }
            )
            renewal_current = 5
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "protocol_login_started")
            db.event(
                f"[{email}] [Session] 复用注册机登录链路更新 AT：协议登录优先，遇到浏览器挑战时由原生无头浏览器接管",
                detail={"email": email, "scope": "selected", "renewal_login_mode": "protocol_native_headless"},
            )
            succeeded, result = _run_one(db, "sunny_login", fallback_payload, mailbox, idx, total_accounts)
            if not succeeded and _is_account_deactivated(result):
                raise RuntimeError(str(result).strip())
            if not succeeded:
                db.ensure_not_cancelled()
                wait_seconds = 15 if _is_otp_security_context_failure(result) else 2
                db.event(
                    f"[{email}] [认证] 协议/原生挑战登录链路未完成，将建立新的隔离无痕后台浏览器上下文重试一次：{result}",
                    "warning",
                    detail={"email": email, "scope": "selected", "renewal_fallback": "background_headless"},
                )
                _emit_renewal_progress(db, email, 6, renewal_total, "headless_login_fallback")
                for _ in range(wait_seconds):
                    db.ensure_not_cancelled()
                    time.sleep(1)
                background_payload = dict(fallback_payload)
                background_payload.update({"execution_mode": "background", "renewal_retry_fresh_context": True})
                succeeded, result = _run_one(db, "sunny_login", background_payload, mailbox, idx, total_accounts)
            if not succeeded and _is_account_deactivated(result):
                raise RuntimeError(str(result).strip())
            if not succeeded and _is_otp_security_context_failure(result):
                db.ensure_not_cancelled()
                db.event(
                    f"[{email}] [认证] 后台登录的邮箱验证码请求被认证证明层拒绝；"
                    "已停止使用旧验证码，将建立新的隔离无痕后台浏览器上下文并等待新验证码后重试一次",
                    "warning",
                    detail={"email": email, "scope": "selected", "renewal_fallback": "fresh_headless_context"},
                )
                _emit_renewal_progress(db, email, 6, renewal_total, "sentinel_login_retry")
                # The next reader filters mail by timestamp. Let the rejected OTP
                # fall outside that window so the retry cannot consume it again.
                for _ in range(15):
                    db.ensure_not_cancelled()
                    time.sleep(1)
                retry_payload = dict(fallback_payload)
                retry_payload["execution_mode"] = "background"
                retry_payload["renewal_retry_fresh_context"] = True
                succeeded, result = _run_one(db, "sunny_login", retry_payload, mailbox, idx, total_accounts)
            if not succeeded:
                result_text = str(result).strip()
                email_prefix = f"[{email}] "
                if result_text.startswith(email_prefix):
                    result_text = result_text[len(email_prefix):].strip()
                raise RuntimeError(result_text)
            renewal_current = 8
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "session_refreshed")
            items.append({"email": email, "has_access_token": bool(isinstance(result, dict) and result.get("has_access_token")), "has_refresh_token": bool(isinstance(result, dict) and result.get("has_refresh_token")), "refresh_method": "headless_login", "refresh_token_error": refresh_error})
            ok += 1
            _account_event(db, email, "session", "access_token.renewed", f"[{email}] [Session] 已通过后台无头登录完成 AT 续期", account_id=int(acc.get("id") or 0))
            renewal_current = 9
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "completed", state="succeeded")
        except Exception as exc:
            if _is_cancel_exception(exc):
                raise
            errors.append(f"[{email}] {exc}")
            if _is_account_deactivated(exc):
                db.mark_account_deactivated(email, str(exc))
                db.event(
                    f"[{email}] [认证] AT 续期确认账户已停用，已归类为已封禁并更新最近测活时间",
                    "warning",
                    detail={"email": email, "scope": "selected", "account_deactivated": True},
                )
                _emit_renewal_progress(db, email, renewal_current, renewal_total, "account_deactivated", state="failed", error=str(exc))
            else:
                db.mark_access_token_renewal_failed(email, str(exc))
                _account_event(db, email, "session", "access_token.renewal_failed", errors[-1], "error", account_id=int(acc.get("id") or 0), detail={"error": str(exc)})
                _emit_renewal_progress(db, email, renewal_current, renewal_total, "failed", state="failed", error=str(exc))
        if not parallel:
            db.update_task(progress_current=idx, success_count=ok, error_count=len(errors))
    return ok, errors, items


def _refresh_sessions_isolated(
    task_id: str,
    payload: dict[str, Any],
    account_id: int,
    index: int,
    total: int,
) -> tuple[int, int, list[str], list[dict[str, Any]]]:
    """Refresh one account with an isolated DB connection and auth context."""
    worker_db = SunnyDB(task_id, ensure_schema=False)
    single_payload = dict(payload)
    single_payload.update(
        {
            "account_ids": [account_id],
            "_renewal_index_offset": index - 1,
            "_renewal_total": total,
            "_renewal_parallel": True,
        }
    )
    try:
        ok, errors, items = _refresh_sessions_sequential(worker_db, single_payload)
        return index, ok, errors, items
    finally:
        worker_db.close()


def _refresh_sessions(db: SunnyDB, payload: dict[str, Any]) -> tuple[int, list[str], list[dict[str, Any]]]:
    accounts = db.fetch_accounts(_ids(payload.get("account_ids")) or None)
    if len(accounts) <= 1:
        return _refresh_sessions_sequential(db, payload)
    requested = int(payload.get("concurrency") or os.getenv("SUNNY_AT_RENEWAL_CONCURRENCY") or 3)
    concurrency = max(1, min(requested, 6, len(accounts)))
    if concurrency <= 1:
        return _refresh_sessions_sequential(db, payload)
    db.event(
        f"[系统] AT续期并发数：{concurrency}，每个账户使用独立 Worker/认证上下文",
        detail={"scope": "global", "concurrency": concurrency, "total": len(accounts), "operation": "access_token_renewal"},
    )
    success = 0
    completed = 0
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for batch_start in range(0, len(accounts), concurrency):
        batch = accounts[batch_start : batch_start + concurrency]
        pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="sunny-renewal")
        try:
            futures = {
                pool.submit(
                    _refresh_sessions_isolated,
                    db.task_id,
                    payload,
                    int(account.get("id") or 0),
                    batch_start + offset,
                    len(accounts),
                ): str(account.get("email") or "")
                for offset, account in enumerate(batch, start=1)
            }
            pending = set(futures)
            while pending:
                if db.cancel_requested():
                    for future in pending:
                        future.cancel()
                    raise SunnyTaskCancelled("Task cancelled by user")
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    try:
                        _index, ok, account_errors, account_items = future.result()
                    except Exception as exc:
                        if _is_cancel_exception(exc):
                            raise
                        ok = 0
                        account_items = []
                        account_errors = [f"[{futures[future]}] AT续期并行 Worker 失败: {exc}"]
                    completed += 1
                    success += ok
                    errors.extend(account_errors)
                    items.extend(account_items)
                    db.update_task(progress_current=completed, success_count=success, error_count=len(errors))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    return success, errors, items


def _acquire_refresh_tokens(db: SunnyDB, payload: dict[str, Any]) -> tuple[int, list[str], list[dict[str, Any]]]:
    accounts = db.fetch_accounts(_ids(payload.get("account_ids")) or None)
    ok = 0
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for idx, acc in enumerate(accounts, start=1):
        db.ensure_not_cancelled()
        email = str(acc.get("email") or "")
        try:
            existing_rt = str(acc.get("openai_rt") or "").strip()
            if not existing_rt:
                saved_session = db.fetch_session_by_email(email) or {}
                existing_rt = str(saved_session.get("refresh_token") or "").strip()
            if existing_rt:
                ok += 1
                items.append({"email": email, "has_refresh_token": True, "acquire_method": "existing"})
                _account_event(db, email, "session", "refresh_token.present", f"[{email}] [Session] 账户已有 Refresh Token，无需重复授权", account_id=int(acc.get("id") or 0))
                db.update_task(progress_current=idx, success_count=ok, error_count=len(errors))
                continue

            mailbox = db.fetch_mailbox_by_email(email)
            if not mailbox:
                raise RuntimeError("找不到该账户对应的邮箱凭证")
            acquire_payload = dict(payload)
            acquire_payload.update({
                "execution_mode": "background",
                "registration_stage": CODEX_PHONE_BIND,
                "mailbox_ids": [int(mailbox.get("id") or 0)],
            })
            succeeded, result = _run_one(db, "sunny_acquire_rt", acquire_payload, mailbox, idx, len(accounts))
            result_map = result if isinstance(result, dict) else {}
            if not succeeded or not result_map.get("has_refresh_token"):
                detail = str(result_map.get("stage_error") or result_map.get("phone_skipped_reason") or result)
                raise RuntimeError(detail if detail and detail != "{}" else "无法获取该账户RT")
            ok += 1
            items.append({"email": email, "has_refresh_token": True, "acquire_method": "codex_oauth"})
            _account_event(db, email, "session", "refresh_token.acquired", f"[{email}] [Session] 已通过 Codex OAuth 授权获取 Refresh Token", account_id=int(acc.get("id") or 0))
        except Exception as exc:
            message = str(exc).strip()
            error = f"[{email}] 无法获取该账户RT" + (f"：{message}" if message else "")
            errors.append(error)
            _account_event(db, email, "session", "refresh_token.acquire_failed", error, "error", account_id=int(acc.get("id") or 0), detail={"error": message})
        db.update_task(progress_current=idx, success_count=ok, error_count=len(errors))
    return ok, errors, items


def _add_login_secrets(db: SunnyDB, payload: dict[str, Any]) -> tuple[int, list[str], list[dict[str, Any]]]:
    accounts = db.fetch_accounts(_ids(payload.get("account_ids")) or None)
    success = 0
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for idx, account_row in enumerate(accounts, start=1):
        db.ensure_not_cancelled()
        email = str(account_row.get("email") or "").strip()
        mailbox = db.fetch_mailbox_by_email(email)
        if not mailbox:
            error = f"[{email}] 找不到对应的邮箱凭证"
            errors.append(error)
            db.event(error, "error", detail={"email": email, "scope": "selected"})
            db.update_task(progress_current=idx, success_count=success, error_count=len(errors))
            continue
        chatgpt_password = str(mailbox.get("chat_gpt_password") or "").strip()
        totp_secret = str(mailbox.get("totp_secret") or "").strip()
        if chatgpt_password and totp_secret:
            success += 1
            items.append({"email": email, "status": "skipped", "login_secret_complete": True})
            db.event(f"[{email}] [登录密钥] 已存在完整 LS，跳过重复设置", detail={"email": email, "scope": "selected"})
            db.update_task(progress_current=idx, success_count=success, error_count=len(errors))
            continue
        try:
            proxies = _prepare_register_proxy(db, payload, email, idx - 1)
            auxiliary_proxy = _auxiliary_proxy(payload, proxies)
            account = account_from_row(mailbox)
            mailbox_proxy_url = _mailbox_proxy_for_task(payload, proxies, auxiliary_proxy, account.mailbox_type)
            session = db.fetch_session_by_email(email) or {}
            meter = ProxyTrafficMeter(
                proxy_url=str(proxies.get("register") or ""),
                tracked_proxy=str(proxies.get("mode") or "") == "proxy_pool",
                email=email,
                operation="sunny_add_ls",
            )
            try:
                result = setup_login_secret(
                    account,
                    session,
                    str(proxies.get("register") or ""),
                    lambda message: db.event(message, detail={"email": email, "scope": "selected"}),
                    should_cancel=db.cancel_requested,
                    mailbox_proxy_url=mailbox_proxy_url,
                    traffic_meter=meter,
                )
            except RuntimeError as exc:
                if "登录态" not in str(exc):
                    raise
                db.event(f"[{email}] [登录密钥] 现有 Session 不可复用，先重新登录账户", "warning", detail={"email": email, "scope": "selected"})
                session = login_or_register(
                    account,
                    str(proxies.get("register") or ""),
                    True,
                    lambda message: db.event(message, detail={"email": email, "scope": "selected"}),
                    existing_account=True,
                    require_refresh_token=False,
                    should_cancel=db.cancel_requested,
                    execution_mode="background",
                    mailbox_proxy_url=mailbox_proxy_url,
                    traffic_meter=meter,
                    traffic_config=payload.get("browser_traffic_optimization"),
                )
                result = setup_login_secret(
                    account,
                    session,
                    str(proxies.get("register") or ""),
                    lambda message: db.event(message, detail={"email": email, "scope": "selected"}),
                    should_cancel=db.cancel_requested,
                    mailbox_proxy_url=mailbox_proxy_url,
                    traffic_meter=meter,
                )
            if result.get("password_added"):
                db.save_chatgpt_password(int(mailbox.get("id") or 0), str(result.get("password") or ""))
            if result.get("totp_added"):
                db.save_totp_secret(int(mailbox.get("id") or 0), str(result.get("totp_secret") or ""))
            refreshed_session = result.get("session") if isinstance(result.get("session"), dict) else session
            if refreshed_session:
                db.upsert_session(email, int(account_row.get("id") or 0), refreshed_session, str(mailbox.get("raw") or ""))
                access_token = str(refreshed_session.get("access_token") or "")
                if access_token:
                    db.upsert_account(email, access_token=access_token, last_error="")
            complete = bool(result.get("complete"))
            items.append({"email": email, "status": "success" if complete else "partial", "login_secret_complete": complete, "errors": list(result.get("errors") or [])})
            if complete:
                success += 1
                db.event(f"[{email}] [登录密钥] LS 添加完成", detail={"email": email, "scope": "selected"})
            else:
                message = "；".join(result.get("errors") or ["登录密钥未完整设置"])
                errors.append(f"[{email}] {message}")
                db.event(f"[{email}] [登录密钥] 部分设置未完成: {message}", "warning", detail={"email": email, "scope": "selected"})
            snapshot = meter.snapshot()
            db.record_proxy_traffic(email, int(mailbox.get("id") or 0), int(snapshot.get("total_bytes") or 0))
        except Exception as exc:
            if _is_cancel_exception(exc):
                raise
            error = f"[{email}] 添加 LS 失败: {exc}"
            errors.append(error)
            items.append({"email": email, "status": "failed", "login_secret_complete": False, "error": str(exc)})
            db.event(error, "error", detail={"email": email, "scope": "selected"})
        db.update_task(progress_current=idx, success_count=success, error_count=len(errors))
    return success, errors, items


def run_sunny_task(task_id: str) -> None:
    db = SunnyDB(task_id)
    try:
        task = db.task()
        task_type = task.get("type") or "sunny_register"
        payload = json.loads(task.get("payload_json") or "{}")
        if db.cancel_requested():
            db.mark_cancelled("用户已中断注册任务")
            return
        db.update_task(status="running", started_at=now_sql())
        db.ensure_not_cancelled()
        db.event(f"========= SunnyRegister 注册任务开始 {now_sql()} =========", level="separator", detail={"scope": "global", "separator": True})
        db.event("SunnyRegister Worker accepted register task", typ="state")
        if task_type == "sunny_refresh_session":
            ok, errors, items = _refresh_sessions(db, payload)
            db.ensure_not_cancelled()
            status = "succeeded" if ok else "failed"
            db.update_task(status=status, success_count=ok, error_count=len(errors), result_json=json.dumps({"success": ok, "errors": errors, "items": items}, ensure_ascii=False), error="; ".join(errors[:3]) if not ok else "", finished_at=now_sql())
            return
        if task_type == "sunny_acquire_rt":
            ok, errors, items = _acquire_refresh_tokens(db, payload)
            db.ensure_not_cancelled()
            status = "succeeded" if ok else "failed"
            db.update_task(status=status, success_count=ok, error_count=len(errors), result_json=json.dumps({"success": ok, "errors": errors, "items": items}, ensure_ascii=False), error="; ".join(errors[:3]) if not ok else "", finished_at=now_sql())
            return
        if task_type == "sunny_add_ls":
            ok, errors, items = _add_login_secrets(db, payload)
            db.ensure_not_cancelled()
            skipped = len([item for item in items if item.get("status") == "skipped"])
            partial = len([item for item in items if item.get("status") == "partial"])
            status = "succeeded" if ok else "failed"
            result = {"success": ok, "failed": len(errors), "skipped": skipped, "partial": partial, "errors": errors, "items": items}
            db.update_task(status=status, success_count=ok, error_count=len(errors), result_json=json.dumps(result, ensure_ascii=False), error="; ".join(errors[:3]) if not ok else "", finished_at=now_sql())
            db.event(f"添加 LS 任务总结：成功 {ok}，跳过 {skipped}，部分完成 {partial}，失败 {len(errors)}", "info" if ok else "error", detail={"scope": "global", **result})
            return

        mailboxes = _choose_mailboxes(db, payload)
        if not mailboxes:
            raise RuntimeError("邮箱配置不可用：请先导入并启用 Outlook 邮箱池")
        total = len(mailboxes)
        stage = _stage(payload)
        db.update_task(progress_total=total)
        db.event(f"[系统] 本次任务阶段：{_stage_label(stage)}，账号数量：{total}", detail={"scope": "global", "stage": stage, "total": total})
        _log_proxy_startup(db, payload)
        db.ensure_not_cancelled()
        if payload.get("proxy_enabled") is not False and not _proxy_snapshot(payload).get("register"):
            raise RuntimeError("代理开关已开启，但没有可用于注册机的启用代理；请在代理配置中新增并启用代理，或关闭代理开关后再开始任务")
        requested_concurrency = int(payload.get("concurrency") or 1)
        concurrency = max(1, min(requested_concurrency, total))
        url_api_count = sum(
            1
            for item in mailboxes
            if str(item.get("mailbox_channel") or "").strip().lower() == "url_api"
        )
        if url_api_count == total and concurrency > 3:
            db.event(
                f"[系统] 本批次全部使用 url_api 邮箱，为避免取码服务被高并发轮询压垮，并发数由 {concurrency} 限制为 3",
                "warning",
                detail={
                    "scope": "global",
                    "requested_concurrency": concurrency,
                    "effective_concurrency": 3,
                    "mailbox_channel": "url_api",
                },
            )
            concurrency = 3
        db.event(
            f"[系统] 注册任务并发数：{concurrency}，每个邮箱使用独立 Worker/浏览器上下文/邮箱验证码读取器",
            detail={"scope": "global", "concurrency": concurrency, "total": total},
        )
        success = 0
        completed = 0
        errors: list[str] = []
        items: list[dict[str, Any]] = []
        protocol_batch_policy = _ProtocolBatchPolicy()
        if concurrency <= 1:
            for idx, mailbox in enumerate(mailboxes, start=1):
                db.ensure_not_cancelled()
                ok, result = _run_one(db, task_type, payload, mailbox, idx, total, protocol_batch_policy)
                db.ensure_not_cancelled()
                completed += 1
                if ok:
                    success += 1
                    assert isinstance(result, dict)
                    items.append(result)
                else:
                    errors.append(str(result))
                db.update_task(progress_current=completed, success_count=success, error_count=len(errors))
        else:
            pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="sunny-register")
            try:
                futures = [
                    pool.submit(_run_one_isolated, db.task_id, task_type, payload, mailbox, idx, total, protocol_batch_policy)
                    for idx, mailbox in enumerate(mailboxes, start=1)
                ]
                pending = set(futures)
                while pending:
                    if db.cancel_requested():
                        for future in pending:
                            future.cancel()
                        db.mark_cancelled("用户已中断注册任务")
                        return
                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    if not done:
                        continue
                    for future in done:
                        completed += 1
                        try:
                            _idx, ok, result = future.result()
                        except Exception as exc:
                            if _is_cancel_exception(exc):
                                db.mark_cancelled("用户已中断注册任务")
                                return
                            ok, result = False, f"parallel worker failed: {exc}"
                        db.ensure_not_cancelled()
                        if ok:
                            success += 1
                            assert isinstance(result, dict)
                            items.append(result)
                        else:
                            errors.append(str(result))
                        db.update_task(progress_current=completed, success_count=success, error_count=len(errors))
            finally:
                # Do not let browser/OTP threads outlive the task. Running flows
                # observe the cancelled task state through should_cancel and then
                # close their own browser, mailbox reader and DB connection.
                pool.shutdown(wait=True, cancel_futures=True)
        db.ensure_not_cancelled()
        registered = len([x for x in items if x.get("auth_action") == "register"])
        logged_in = len([x for x in items if x.get("auth_action") != "register"])
        skipped_phone = len([x for x in items if x.get("phone_skipped_reason")])
        imported = len([x for x in items if x.get("sub2api")])
        partial = len([x for x in items if x.get("stage_complete") is False])
        status = "succeeded" if success else "failed"
        summary = {"success": success, "failed": len(errors), "partial": partial, "registered": registered, "logged_in": logged_in, "skipped_phone": skipped_phone, "imported": imported, "stage": stage, "errors": errors, "items": items}
        db.update_task(status=status, error="; ".join(errors[:3]) if not success else "", result_json=json.dumps(summary, ensure_ascii=False), finished_at=now_sql())
        db.event(f"注册任务总结：成功 {success}，失败 {len(errors)}，阶段未完成 {partial}，新注册 {registered}，登录更新 {logged_in}，跳过接码 {skipped_phone}，导入反代 {imported}", "info" if success else "error", detail={"scope": "global", **summary})
    except Exception as exc:
        if _is_cancel_exception(exc):
            db.mark_cancelled("用户已中断注册任务")
            return
        db.update_task(status="failed", error=f"SunnyRegister Worker failed: {exc}", result_json=json.dumps({"traceback": traceback.format_exc()[-4000:]}, ensure_ascii=False), finished_at=now_sql())
        db.event(f"SunnyRegister Worker failed: {exc}", "error", detail={"traceback": traceback.format_exc()[-4000:]})
    finally:
        db.close()
