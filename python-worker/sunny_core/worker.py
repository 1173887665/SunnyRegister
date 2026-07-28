from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .agent_identity import AgentIdentityUnavailableError, create_agent_identity_auth
from .db import SunnyDB, SunnyTaskCancelled, now_sql
from .mailbox import account_from_row, parse_account_line
from .openai_auth import TaskCancelledError, login_or_register, refresh_openai_access_token
from .phone_pool import wait_sms_code
from .protocol_auth import ProtocolChallengeRequired, login_or_register_protocol
from .proxy import build_proxy, proxy_target_tls_check, redact_proxy_url
from .smsbower import SMSBowerClient
from .smspool import SMSPoolClient

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
) -> None:
    total = _registration_stage_total(stage)
    current = min(total, max(0, _REGISTRATION_PROGRESS_STEPS.get(checkpoint, 0)))
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


def _is_cancel_exception(exc: BaseException) -> bool:
    return isinstance(exc, (SunnyTaskCancelled, TaskCancelledError)) or "Task cancelled by user" in str(exc)


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


def _proxy_snapshot(payload: dict[str, Any], slot: int = 0) -> dict[str, str]:
    if payload.get("proxy_enabled") is False:
        system_proxy = str(payload.get("system_proxy") or "").strip()
        normalized_system_proxy = _container_host_proxy(build_proxy("", system_proxy).url)
        return {"register": normalized_system_proxy, "mode": "system_proxy" if normalized_system_proxy else "direct", "local_proxy": ""}
    base = str(payload.get("proxy") or "").strip()
    raw_pool = payload.get("proxy_pool")
    pool_items = raw_pool if isinstance(raw_pool, list) else []
    pool = [_container_host_proxy(build_proxy("", str(item or "")).url) for item in pool_items if str(item or "").strip()]
    if pool:
        register_proxy = pool[max(0, int(slot)) % len(pool)]
    else:
        register_proxy = _container_host_proxy(build_proxy("", str(payload.get("register_proxy") or base)).url)
    local_proxy = _container_host_proxy(build_proxy(str(payload.get("local_proxy") or ""), "").url)
    return {"register": register_proxy, "mode": "proxy_pool", "local_proxy": local_proxy}


def _prepare_register_proxy(db: SunnyDB, payload: dict[str, Any], email: str, slot: int = 0) -> dict[str, str]:
    proxies = _proxy_snapshot(payload, slot)
    proxy = proxies.get("register", "")
    if not proxy or proxies.get("mode") != "proxy_pool":
        return proxies
    check = proxy_target_tls_check(proxy, timeout=10)
    if check.get("ok"):
        db.event(
            f"[{email}] [代理] 代理 HTTPS 隧道预检通过：{redact_proxy_url(proxy)}，延迟 {check.get('latency_ms', 0)}ms",
            detail={"email": email, "scope": "selected", "proxy": proxy, "proxy_mode": proxies.get("mode"), "proxy_precheck": check},
        )
        return proxies
    err = str(check.get("error") or "unknown error")
    db.event(
        f"[{email}] [代理] 直接代理无法建立到 chatgpt.com:443 的 HTTPS 隧道：{redact_proxy_url(proxy)}；原因：{err}",
        "warning",
        detail={"email": email, "scope": "selected", "proxy": proxy, "proxy_mode": proxies.get("mode"), "proxy_precheck": check},
    )
    local_proxy = proxies.get("local_proxy", "")
    if local_proxy and local_proxy != proxy:
        local_check = proxy_target_tls_check(local_proxy, timeout=10)
        if local_check.get("ok"):
            db.event(
                f"[{email}] [代理] 已自动回退到本地代理出口：{redact_proxy_url(local_proxy)}。该模式适合 Clash/Surge 等本地代理继续链式转发到静态住宅 IP。",
                "warning",
                detail={"email": email, "scope": "selected", "proxy": local_proxy, "proxy_mode": "local_proxy_fallback", "proxy_precheck": local_check},
            )
            return {"register": local_proxy, "mode": "local_proxy_fallback", "local_proxy": local_proxy}
        db.event(
            f"[{email}] [代理] 本地代理出口也未通过 HTTPS 隧道预检：{redact_proxy_url(local_proxy)}；原因：{local_check.get('error') or 'unknown error'}",
            "warning",
            detail={"email": email, "scope": "selected", "proxy": local_proxy, "proxy_mode": "local_proxy_fallback", "proxy_precheck": local_check},
        )
    raise RuntimeError(f"代理不可用于 ChatGPT 注册链路：{redact_proxy_url(proxy)}；{err}")


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


def _smsbower_provider(db: SunnyDB, email: str, proxy_url: str = ""):
    active: dict[str, Any] = {}
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    client = SMSBowerClient(db.get_config("phone"), proxies=proxies)

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active
        if action == "next":
            activation = client.get_number()
            active = {
                "provider": "smsbower",
                "activation_id": activation.activation_id,
                "number": activation.number,
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


def _smspool_provider(db: SunnyDB, email: str, proxy_url: str = ""):
    active: dict[str, Any] = {}
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    phone_cfg = db.get_config("phone")
    country_value = str(phone_cfg.get("smspool_default_country") or "1").strip()
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
    country_extra = db.sms_provider_option_extra(country_option)
    client = SMSPoolClient(phone_cfg, proxies=proxies)

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active
        if action == "next":
            db.event(
                f"[{email}] [接码] 准备向 SMSPool 申请手机号：country={client.country}，service={client.service}，pool={client.pool or '-'}，max_price={client.max_price}",
                detail={"email": email, "scope": "selected", "sms_provider": "smspool", "country": client.country, "service": client.service, "pool": client.pool, "max_price": client.max_price},
            )
            reusable = db.reserve_sms_provider_number("smspool", client.country, client.service)
            preferred_number = str((reusable or {}).get("phone_number") or "")
            if preferred_number:
                db.event(
                    f"[{email}] [接码] SMSPool 尝试复用冷却已结束的手机号 {preferred_number}",
                    detail={"email": email, "scope": "selected", "sms_provider": "smspool", "reuse": True},
                )
            try:
                activation = client.get_number(preferred_number=preferred_number)
            except Exception as exc:
                if preferred_number:
                    db.mark_sms_provider_number_error("smspool", preferred_number, str(exc))
                    db.event(
                        f"[{email}] [接码] SMSPool 复用手机号失败，将重新购买新号码：{exc}",
                        "warning",
                        detail={"email": email, "scope": "selected", "sms_provider": "smspool", "reuse": True},
                    )
                    try:
                        activation = client.get_number()
                    except Exception as fallback_exc:
                        db.event(
                            f"[{email}] [接码] SMSPool 重新购买手机号失败：{fallback_exc}",
                            "error",
                            detail={"email": email, "scope": "selected", "sms_provider": "smspool"},
                        )
                        raise
                else:
                    db.event(
                        f"[{email}] [接码] SMSPool 申请手机号失败：{exc}",
                        "error",
                        detail={"email": email, "scope": "selected", "sms_provider": "smspool"},
                    )
                    raise
            active = {
                "provider": "smspool",
                "order_id": activation.order_id,
                "activation_id": activation.order_id,
                "number": activation.number,
                "token": activation.token,
                "country": client.country,
                "country_iso": str(country_extra.get("short_name") or ""),
                "country_name": str(country_extra.get("name") or (country_option or {}).get("label") or ""),
                "country_code": str(country_extra.get("cc") or ""),
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
                f"[{email}] [接码] 已从 SMSPool 获取手机号 {activation.number}，订单 ID {activation.order_id}",
                detail={"email": email, "scope": "selected", "sms_provider": "smspool"},
            )
            return active
        if action == "code":
            phone = payload or active
            order_id = str(phone.get("order_id") or phone.get("activation_id") or "")
            return client.wait_code(
                order_id,
                timeout=180,
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
            try:
                client.cancel(order_id)
            finally:
                db.mark_sms_provider_number_error("smspool", str(phone.get("number") or ""), str(phone.get("error") or "SMSPool order failed"))
                db.event(f"[{email}] [接码] SMSPool 接码订单已取消", "warning", detail={"email": email, "scope": "selected", "sms_provider": "smspool"})
            return True
        return None

    return provider


def _combined_phone_provider(db: SunnyDB, email: str, proxy_url: str = ""):
    candidates: list[tuple[str, Any]] = []
    if db.smsbower_available():
        candidates.append(("SMSBower", lambda: _smsbower_provider(db, email, proxy_url)))
    if db.smspool_available():
        candidates.append(("SMSPool", lambda: _smspool_provider(db, email, proxy_url)))
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

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active_provider, active_name, active_phone
        if action == "next":
            active_provider = None
            active_name = ""
            active_phone = {}
            while remaining:
                name, factory = remaining.pop(0)
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
                    return active_phone
                except Exception as exc:
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
        try:
            return active_provider(action, _email, payload or active_phone)
        finally:
            if action == "bad":
                db.event(
                    f"[{email}] [接码] {active_name} 本次号码失败，将切换到下一个接码资源",
                    "warning",
                    detail={"email": email, "scope": "selected", "sms_provider": active_name},
                )
                active_provider = None
                active_name = ""
                active_phone = {}

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


def _import_sub2api(db: SunnyDB, email: str, account_id: int, session: dict[str, Any]) -> dict[str, Any]:
    cfg, base_url, token = _sub2api_config(db)
    refresh_token = str(session.get("refresh_token") or session.get("openai_rt") or "").strip()
    if not refresh_token:
        raise RuntimeError("当前账号没有 Refresh Token，无法导入 sub2api")
    token_record = session.get("token_record")
    if not isinstance(token_record, dict):
        token_record = {}
    credentials = {
        "access_token": session.get("access_token", ""),
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
    payload = {
        "name": f"{str(cfg.get('name_prefix') or '')}{email}",
        "platform": "openai",
        "type": "oauth",
        "credentials": credentials,
        "extra": {"import_source": "sunnyregister_oauth_code", "email": email},
        "group_ids": _sub2api_group_ids(cfg.get("group_ids")),
        "concurrency": int(cfg.get("concurrency") or 3),
        "priority": int(cfg.get("priority") or 50),
    }
    resp = requests.post(f"{base_url}/api/v1/admin/accounts", headers={"x-api-key": token}, json=payload, timeout=60)
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"sub2api 导入失败: HTTP {resp.status_code} {resp.text[:500]}")
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    db.set_account_sub2api_status(email, "imported", str(data.get("id") or data.get("data", {}).get("id") or ""))
    db.event(f"[{email}] [反代] 已根据反代配置导入 sub2api", detail={"email": email, "scope": "selected", "account_id": account_id})
    return data


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
            data = _import_sub2api(db, email, account_id, session)
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
    resp = _post_sub2api_agent_identity(db, endpoint, headers, payload)
    if resp.status_code in {400, 404, 422} and "content" in str(resp.text or "").lower():
        # Older Sub2API builds accepted a single content field. Only retry
        # schema-level rejections, so a successful import is never duplicated.
        legacy_payload = {**payload, "content": auth_content}
        legacy_payload.pop("contents", None)
        resp = _post_sub2api_agent_identity(db, endpoint, headers, legacy_payload)
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


def _run_one(db: SunnyDB, task_type: str, payload: dict[str, Any], mailbox: dict[str, Any], index: int, total: int) -> tuple[bool, dict[str, Any] | str]:
    db.ensure_not_cancelled()
    email = mailbox.get("email") or f"mailbox-{index}"
    stage = _stage(payload)
    explicit_rt_acquire = task_type == "sunny_acquire_rt"
    _emit_registration_progress(db, str(email), stage, "initializing")
    proxies = _prepare_register_proxy(db, payload, str(email), index - 1)
    execution_mode = str(payload.get("execution_mode") or payload.get("mode") or "background").strip().lower()
    if execution_mode not in {"background", "visible", "protocol"}:
        execution_mode = "background"
    headless = execution_mode == "background"
    protocol_challenge_strategy = str(payload.get("protocol_challenge_strategy") or "native_headless").strip().lower()
    if protocol_challenge_strategy not in {"native_headless", "sentinel_protocol"}:
        protocol_challenge_strategy = "native_headless"
    account = account_from_row(mailbox)
    mailbox_id = max(0, int(mailbox.get("id") or 0))
    is_registered_mailbox = bool(account.openai_rt) or str(mailbox.get("status") or "") in {"registered", "已注册", "phone_bound", "已接码", "已反代", "reverse_proxied", "登录刷新"}
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
    _emit_registration_progress(db, str(email), stage, "proxy_ready")
    db.mark_mailbox(mailbox_id, "登录刷新" if is_registered_mailbox else "注册中")

    def save_progress(checkpoint: str, snapshot: dict[str, Any]) -> None:
        _emit_registration_progress(db, str(email), stage, checkpoint)
        if checkpoint in {"registered", "phone_bound"}:
            _persist_registration_checkpoint(
                db,
                mailbox,
                account,
                checkpoint,
                snapshot,
                original_mailbox_status,
            )

    wants_rt = stage in {CODEX_PHONE_BIND, IMPORT_REVERSE_PROXY} or explicit_rt_acquire
    phone_provider = None
    require_refresh_token = False
    phone_skipped_reason = ""
    if wants_rt:
        sms_cfg = db.get_config("phone")
        db.event(
            f"[{email}] [接码] 接码资源检查：自建号池可用 {db.usable_phone_count()} 个，SMSBower={'启用' if db.smsbower_available() else '不可用'}，SMSPool={'启用' if db.smspool_available() else '不可用'}",
            detail={"email": email, "scope": "selected", "sms_provider": "resource_check", "phone_config": {"pool_enabled": sms_cfg.get("pool_enabled"), "smsbower_enabled": sms_cfg.get("smsbower_enabled"), "smspool_enabled": sms_cfg.get("smspool_enabled")}},
        )
        if account.openai_rt:
            require_refresh_token = True
            db.event(f"[{email}] [接码] 邮箱记录已有 OpenAI RT，将直接刷新 Session", detail={"email": email, "scope": "selected"})
        else:
            phone_provider = _combined_phone_provider(db, email, proxies.get("register", ""))
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
            phone_skipped_reason = "无可用手机号：自建手机号池未开启/无可用号码，且 SMSBower/SMSPool 未启用或未配置 API Key。本账号只执行 ChatGPT 注册/登录，不进行接码，也不会获取 Refresh Token。"
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
        if execution_mode == "protocol":
            try:
                session = login_or_register_protocol(
                    account,
                    proxies["register"],
                    lambda m: db.event(m, detail={"email": email, "scope": "selected"}),
                    existing_account=is_registered_mailbox or task_type == "sunny_login",
                    should_cancel=db.cancel_requested,
                    on_progress=save_progress,
                    challenge_strategy=protocol_challenge_strategy,
                )
            except ProtocolChallengeRequired as challenge:
                db.ensure_not_cancelled()
                protocol_traffic = getattr(challenge, "traffic", None)
                if protocol_challenge_strategy == "sentinel_protocol":
                    db.event(
                        f"[{email}] [认证] Sentinel 协议运行时未能生成有效证明，任务不会切换到完整浏览器接管: {challenge}",
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
                db.event(
                    f"[{email}] [认证] 协议模式遇到浏览器挑战，切换到后台无头浏览器继续注册/登录",
                    "warning",
                    detail={
                        "email": email,
                        "scope": "selected",
                        "execution_mode": "protocol_headless_fallback",
                        "protocol_error": str(challenge),
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
                protocol_session = session
                if wants_rt and require_refresh_token:
                    db.event(
                        f"[{email}] [认证] 协议注册/登录已完成，转入后台 OAuth 续段以完成接码和 Refresh Token 获取",
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
            )
        db.ensure_not_cancelled()
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
        if isinstance(session.get("protocol_traffic"), dict):
            result["protocol_traffic"] = session["protocol_traffic"]
        if session.get("protocol_fallback"):
            result["protocol_fallback"] = str(session["protocol_fallback"])
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
                db.event(f"[{email}] [反代] 没有 Refresh Token，已停止导入 sub2api", "warning", detail={"email": email, "scope": "selected"})
            else:
                try:
                    _emit_registration_progress(db, str(email), stage, "reverse_importing")
                    result["sub2api"] = _import_sub2api(db, email, account_id, session)
                    mailbox_status = _highest_mailbox_progress(mailbox_status, "已反代")
                    db.mark_mailbox(mailbox_id, mailbox_status, openai_rt=rt_value)
                    db.upsert_account(email, mailbox_id=mailbox_id, status="reverse_proxied", last_error="")
                    result["completed_status"] = mailbox_status
                    result["stage_complete"] = True
                    _emit_registration_progress(db, str(email), stage, "reverse_imported")
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
                _emit_registration_progress(db, str(email), stage, "agent_identity_importing")
                import_result = _import_sub2api_agent_identity(
                    db,
                    email,
                    account_id,
                    session,
                    proxies.get("register", ""),
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
                _emit_registration_progress(db, str(email), stage, "agent_identity_imported")
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
        result["has_access_token"] = bool(result.pop("access_token", ""))
        result["has_refresh_token"] = bool(result.pop("refresh_token", ""))
        terminal_checkpoint = {
            REGISTER_ONLY: "registered",
            CODEX_PHONE_BIND: "phone_bound",
            IMPORT_REVERSE_PROXY: "reverse_imported",
            AGENT_IDENTITY_REVERSE_PROXY: "agent_identity_imported",
        }.get(stage, "registered")
        _emit_registration_progress(
            db,
            str(email),
            stage,
            terminal_checkpoint if result.get("stage_complete") else "stage_incomplete",
            state="completed" if result.get("stage_complete") else "abnormal",
            error=str(result.get("stage_error") or ""),
        )
        return True, result
    except Exception as exc:
        if _is_cancel_exception(exc):
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
        traffic = getattr(exc, "traffic", None)
        if "Phone verification required" in err_text or "phone verification" in err_text.lower():
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
        db.event(err, "error", detail=error_detail)
        _emit_registration_progress(db, str(email), stage, "failed", state="abnormal", error=err_text)
        return False, err


def _run_one_isolated(task_id: str, task_type: str, payload: dict[str, Any], mailbox: dict[str, Any], index: int, total: int) -> tuple[int, bool, dict[str, Any] | str]:
    """Run one mailbox in its own DB connection/thread.

    Each browser flow owns exactly one mailbox/account object, one Outlook reader,
    one browser context and one SQLite connection. This keeps concurrent OTP reads
    and mailbox state updates isolated from other mailboxes.
    """
    worker_db = SunnyDB(task_id, ensure_schema=False)
    try:
        ok, result = _run_one(worker_db, task_type, payload, mailbox, index, total)
        return index, ok, result
    finally:
        worker_db.close()


def _refresh_sessions(db: SunnyDB, payload: dict[str, Any]) -> tuple[int, list[str], list[dict[str, Any]]]:
    accounts = db.fetch_accounts(_ids(payload.get("account_ids")) or None)
    ok = 0
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for idx, acc in enumerate(accounts, start=1):
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
                    db.event(f"[{email}] [Session] 已通过 Refresh Token 完成 AT 续期")
                    renewal_current = 7
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "completed", state="succeeded")
                    db.update_task(progress_current=idx, success_count=ok, error_count=len(errors))
                    continue
                except Exception as exc:
                    refresh_error = str(exc)
                    renewal_total = 9
                    renewal_current = 3
                    _emit_renewal_progress(db, email, renewal_current, renewal_total, "refresh_token_unavailable")
                    db.event(f"[{email}] [Session] Refresh Token 续期不可用，改用后台无头登录更新 AT：{refresh_error}", "warning")
            else:
                renewal_total = 9
                renewal_current = 3
                _emit_renewal_progress(db, email, renewal_current, renewal_total, "refresh_token_missing")
                db.event(f"[{email}] [Session] 账户没有可用 Refresh Token，改用后台无头登录更新 AT", "warning")

            if not mailbox:
                raise RuntimeError("找不到该账户对应的邮箱凭证，无法回退登录更新 AT")
            renewal_current = 4
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "mailbox_ready")
            fallback_payload = dict(payload)
            fallback_payload.update({"execution_mode": "background", "registration_stage": "register_only", "mailbox_ids": [int(mailbox.get("id") or 0)]})
            renewal_current = 5
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "headless_login_started")
            succeeded, result = _run_one(db, "sunny_login", fallback_payload, mailbox, idx, len(accounts))
            if not succeeded:
                raise RuntimeError(str(result))
            renewal_current = 8
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "session_refreshed")
            items.append({"email": email, "has_access_token": bool(isinstance(result, dict) and result.get("has_access_token")), "has_refresh_token": bool(isinstance(result, dict) and result.get("has_refresh_token")), "refresh_method": "headless_login", "refresh_token_error": refresh_error})
            ok += 1
            db.event(f"[{email}] [Session] 已通过后台无头登录完成 AT 续期")
            renewal_current = 9
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "completed", state="succeeded")
        except Exception as exc:
            errors.append(f"[{email}] {exc}")
            db.event(errors[-1], "error")
            _emit_renewal_progress(db, email, renewal_current, renewal_total, "failed", state="failed", error=str(exc))
        db.update_task(progress_current=idx, success_count=ok, error_count=len(errors))
    return ok, errors, items


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
                db.event(f"[{email}] [Session] 账户已有 Refresh Token，无需重复授权")
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
            db.event(f"[{email}] [Session] 已通过 Codex OAuth 授权获取 Refresh Token")
        except Exception as exc:
            message = str(exc).strip()
            error = f"[{email}] 无法获取该账户RT" + (f"：{message}" if message else "")
            errors.append(error)
            db.event(error, "error")
        db.update_task(progress_current=idx, success_count=ok, error_count=len(errors))
    return ok, errors, items


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
        db.event(
            f"[系统] 注册任务并发数：{concurrency}，每个邮箱使用独立 Worker/浏览器上下文/邮箱验证码读取器",
            detail={"scope": "global", "concurrency": concurrency, "total": total},
        )
        success = 0
        completed = 0
        errors: list[str] = []
        items: list[dict[str, Any]] = []
        if concurrency <= 1:
            for idx, mailbox in enumerate(mailboxes, start=1):
                db.ensure_not_cancelled()
                ok, result = _run_one(db, task_type, payload, mailbox, idx, total)
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
                    pool.submit(_run_one_isolated, db.task_id, task_type, payload, mailbox, idx, total)
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
