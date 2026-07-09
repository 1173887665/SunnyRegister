from __future__ import annotations

import json
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

import requests

from .db import SunnyDB, SunnyTaskCancelled, now_sql
from .mailbox import account_from_row, parse_account_line
from .openai_auth import TaskCancelledError, login_or_register, refresh_openai_access_token
from .phone_pool import wait_sms_code
from .proxy import build_proxy, proxy_target_tls_check, redact_proxy_url
from .smsbower import SMSBowerClient
from .smspool import SMSPoolClient

REGISTER_ONLY = "register_only"
CODEX_PHONE_BIND = "codex_phone_bind"
IMPORT_REVERSE_PROXY = "import_reverse_proxy"


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
    return value if value in {REGISTER_ONLY, CODEX_PHONE_BIND, IMPORT_REVERSE_PROXY} else REGISTER_ONLY


def _stage_label(stage: str) -> str:
    return {
        REGISTER_ONLY: "仅注册ChatGPT",
        CODEX_PHONE_BIND: "Codex接码绑定",
        IMPORT_REVERSE_PROXY: "导入反代平台",
    }.get(stage, stage)


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


def _proxy_snapshot(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("proxy_enabled") is False:
        system_proxy = str(payload.get("system_proxy") or payload.get("local_proxy") or "").strip()
        return {"register": build_proxy("", system_proxy).url, "mode": "system_proxy" if system_proxy else "direct", "local_proxy": build_proxy(system_proxy, "").url}
    base = str(payload.get("proxy") or "").strip()
    local_proxy = build_proxy(str(payload.get("local_proxy") or ""), "").url
    return {"register": build_proxy("", str(payload.get("register_proxy") or base)).url, "mode": "proxy_pool", "local_proxy": local_proxy}


def _prepare_register_proxy(db: SunnyDB, payload: dict[str, Any], email: str) -> dict[str, str]:
    proxies = _proxy_snapshot(payload)
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
    db.event(
        f"[代理] 代理开关：开启；代理池总数 {total}，启用 {enabled}，停用 {disabled}，失效 {invalid}",
        detail={"scope": "global", "proxy_enabled": True, "proxy_stats": stats},
    )
    if proxy:
        db.event(f"[代理] 注册/登录请求将使用代理出口：{proxy}", detail={"scope": "global", "proxy": proxy})
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
    client = SMSPoolClient(phone_cfg, proxies=proxies)

    def provider(action: str, _email: str, payload: Any = None):
        nonlocal active
        if action == "next":
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
                    activation = client.get_number()
                else:
                    raise
            active = {
                "provider": "smspool",
                "order_id": activation.order_id,
                "activation_id": activation.order_id,
                "number": activation.number,
                "token": activation.token,
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


def _import_sub2api(db: SunnyDB, email: str, account_id: int, session: dict[str, Any]) -> dict[str, Any]:
    cfg = db.get_config("sub2api")
    if cfg.get("enabled") is False:
        raise RuntimeError("反代配置中的 sub2api 未启用")
    base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
    token = str(cfg.get("admin_token") or "").strip()
    if not base_url or not token:
        raise RuntimeError("请先在反代配置中填写 sub2api Base URL 和 Admin Token")
    refresh_token = str(session.get("refresh_token") or session.get("openai_rt") or "").strip()
    if not refresh_token:
        raise RuntimeError("当前账号没有 Refresh Token，无法导入 sub2api")
    payload = {
        "name": f"{str(cfg.get('name_prefix') or '')}{email}",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": session.get("access_token", ""),
            "refresh_token": refresh_token,
            "id_token": session.get("id_token", ""),
            "email": email,
        },
        "extra": {"import_source": "sunnyregister", "email": email},
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


def _run_one(db: SunnyDB, task_type: str, payload: dict[str, Any], mailbox: dict[str, Any], index: int, total: int) -> tuple[bool, dict[str, Any] | str]:
    db.ensure_not_cancelled()
    email = mailbox.get("email") or f"mailbox-{index}"
    stage = _stage(payload)
    proxies = _prepare_register_proxy(db, payload, str(email))
    execution_mode = str(payload.get("execution_mode") or payload.get("mode") or "background").strip().lower()
    if execution_mode not in {"background", "visible", "protocol"}:
        execution_mode = "background"
    if execution_mode == "protocol":
        raise RuntimeError("协议模式暂未开放，请选择后台浏览器自动或可视浏览器自动")
    headless = bool(payload.get("headless", execution_mode != "visible"))
    account = account_from_row(mailbox)
    mailbox_id = max(0, int(mailbox.get("id") or 0))
    is_registered_mailbox = bool(account.openai_rt) or str(mailbox.get("status") or "") in {"registered", "已注册", "已接码", "PLUS试用中", "登录刷新"}
    original_mailbox_status = str(mailbox.get("status") or ("已注册" if is_registered_mailbox else "未注册"))
    db.event(f"[{email}] [系统] 开始注册/登录 {index}/{total}，阶段={_stage_label(stage)}", detail={"email": email, "scope": "selected", "stage": stage})
    mode_label = "后台浏览器自动（Headless，无窗口）" if headless else "可视浏览器自动（Visible，有窗口）"
    db.event(f"[{email}] [认证] 执行方式：{mode_label}", detail={"email": email, "scope": "selected", "execution_mode": execution_mode, "headless": headless})
    if proxies.get("register"):
        if proxies.get("mode") == "system_proxy":
            db.event(f"[{email}] [代理] 注册/登录流量使用服务器系统出口代理: {proxies['register']}", detail={"email": email, "scope": "selected", "proxy": proxies["register"], "proxy_mode": "system_proxy"})
        elif proxies.get("mode") == "local_proxy_fallback":
            db.event(f"[{email}] [代理] 注册/登录流量已切换为本地代理链路: {proxies['register']}", detail={"email": email, "scope": "selected", "proxy": proxies["register"], "proxy_mode": "local_proxy_fallback"})
        else:
            db.event(f"[{email}] [代理] 注册/登录流量使用代理池代理: {proxies['register']}（代理池检测为轻量 TCP 连通检测，不等同于目标站点可访问）", detail={"email": email, "scope": "selected", "proxy": proxies["register"], "proxy_mode": "proxy_pool"})
    else:
        db.event(f"[{email}] [代理] 注册/登录流量使用服务器系统网络直连出口", detail={"email": email, "scope": "selected", "proxy": "", "proxy_mode": "direct"})
    db.mark_mailbox(mailbox_id, "登录刷新" if is_registered_mailbox else "注册中")

    wants_rt = stage in {CODEX_PHONE_BIND, IMPORT_REVERSE_PROXY}
    phone_provider = None
    require_refresh_token = False
    phone_skipped_reason = ""
    if wants_rt:
        if account.openai_rt:
            require_refresh_token = True
            db.event(f"[{email}] [接码] 邮箱记录已有 OpenAI RT，将直接刷新 Session", detail={"email": email, "scope": "selected"})
        elif db.usable_phone_count() > 0:
            require_refresh_token = True
            phone_provider = _phone_provider(db, email)
            db.event(f"[{email}] [接码] 将联动“接码配置”的自建手机号池完成手机验证", detail={"email": email, "scope": "selected"})
        elif db.smsbower_available():
            require_refresh_token = True
            phone_provider = _smsbower_provider(db, email, proxies.get("register", ""))
            db.event(f"[{email}] [接码] 自建手机号池无可用号码，将使用 SMSBower 接码供应商完成手机验证", detail={"email": email, "scope": "selected", "sms_provider": "smsbower"})
        elif db.smspool_available():
            require_refresh_token = True
            phone_provider = _smspool_provider(db, email, proxies.get("register", ""))
            db.event(f"[{email}] [接码] 自建手机号池/SMSBower 不可用，将使用 SMSPool 接码供应商完成手机验证", detail={"email": email, "scope": "selected", "sms_provider": "smspool"})
        else:
            phone_skipped_reason = "无可用手机号：自建手机号池未开启/无可用号码，且 SMSBower/SMSPool 未启用或未配置 API Key。本账号只执行 ChatGPT 注册/登录，不进行接码，也不会获取 Refresh Token。"
            db.event(f"[{email}] [接码] {phone_skipped_reason}", "warning", detail={"email": email, "scope": "selected"})

    try:
        db.ensure_not_cancelled()
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
        )
        db.ensure_not_cancelled()
        rt_value = session.get("refresh_token") or session.get("openai_rt") or account.openai_rt
        account_id = db.upsert_account(
            email,
            mailbox_id=mailbox_id,
            status="registered",
            account_type=account.account_type,
            openai_rt=rt_value,
            access_token=session.get("access_token", ""),
            metadata_json=json.dumps({"task_id": db.task_id, "source": "sunny_register", "stage": stage, "phone_skipped_reason": phone_skipped_reason}, ensure_ascii=False),
        )
        db.upsert_session(email, account_id, session, account.raw)
        action = str(session.get("auth_action") or "login")
        action_label = "注册" if action == "register" else "登录"
        has_rt = bool(rt_value)
        mailbox_status = "已接码" if wants_rt and has_rt else "已注册"
        db.mark_mailbox(mailbox_id, mailbox_status, openai_rt=rt_value)
        result: dict[str, Any] = {
            "email": email,
            "account_id": account_id,
            "auth_action": action,
            "stage": stage,
            "access_token": session.get("access_token", ""),
            "refresh_token": rt_value,
            "has_session": bool(session.get("access_token")),
            "phone_skipped_reason": phone_skipped_reason,
        }
        db.event(f"[{email}] [认证] 识别为{action_label}成功，已保存 ChatGPT Session" + (" 和 Refresh Token" if result["refresh_token"] else ""), detail={"email": email, "scope": "selected", **result})
        if stage == IMPORT_REVERSE_PROXY:
            if not result["refresh_token"]:
                result["sub2api_skipped_reason"] = "没有 Refresh Token，已停止导入反代平台"
                db.event(f"[{email}] [反代] 没有 Refresh Token，已停止导入 sub2api", "warning", detail={"email": email, "scope": "selected"})
            else:
                result["sub2api"] = _import_sub2api(db, email, account_id, session)
        return True, result
    except Exception as exc:
        if _is_cancel_exception(exc):
            if mailbox_id > 0:
                db.mark_mailbox(mailbox_id, original_mailbox_status, "用户已中断任务")
            db.event(f"[{email}] [系统] 用户已中断任务，当前邮箱流程已停止", "warning", detail={"email": email, "scope": "selected", "cancelled": True})
            raise
        err_text = str(exc)
        err = f"[{email}] {err_text}"
        if "Phone verification required" in err_text or "phone verification" in err_text.lower():
            db.mark_mailbox(mailbox_id, "需二验", err_text)
            db.event(f"[{email}] [接码] 账号需要手机号二次验证，但当前没有可用接码配置，本账号流程已停止", "warning", detail={"email": email, "scope": "selected"})
        else:
            db.mark_mailbox(mailbox_id, "失败", err_text)
        db.upsert_account(email, mailbox_id=mailbox_id, status="failed", last_error=err_text)
        db.event(err, "error", detail={"email": email, "scope": "selected", "traceback": traceback.format_exc()[-3000:]})
        return False, err


def _run_one_isolated(task_id: str, task_type: str, payload: dict[str, Any], mailbox: dict[str, Any], index: int, total: int) -> tuple[int, bool, dict[str, Any] | str]:
    """Run one mailbox in its own DB connection/thread.

    Each browser flow owns exactly one mailbox/account object, one Outlook reader,
    one browser context and one SQLite connection. This keeps concurrent OTP reads
    and mailbox state updates isolated from other mailboxes.
    """
    worker_db = SunnyDB(task_id)
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
    proxies = _proxy_snapshot(payload)
    for idx, acc in enumerate(accounts, start=1):
        db.ensure_not_cancelled()
        email = acc.get("email") or ""
        try:
            rt = acc.get("openai_rt") or ""
            if not rt:
                sess = db.fetch_session_by_email(email) or {}
                rt = sess.get("refresh_token") or ""
            token = refresh_openai_access_token(rt, proxies["register"])
            db.ensure_not_cancelled()
            account_id = int(acc.get("id") or db.upsert_account(email))
            payload2 = {"access_token": token.get("access_token"), "refresh_token": token.get("refresh_token") or rt, "id_token": token.get("id_token", ""), "session_json": token}
            db.upsert_session(email, account_id, payload2)
            db.upsert_account(email, status="registered", access_token=payload2["access_token"], openai_rt=payload2["refresh_token"])
            db.mark_mailbox_by_email(email, "已接码" if payload2["refresh_token"] else "已注册", openai_rt=payload2["refresh_token"])
            items.append({"email": email, **payload2})
            ok += 1
            db.event(f"[{email}] [Session] Session/RT 刷新完成")
        except Exception as exc:
            errors.append(f"[{email}] {exc}")
            db.event(errors[-1], "error")
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
                pool.shutdown(wait=False, cancel_futures=True)
        db.ensure_not_cancelled()
        registered = len([x for x in items if x.get("auth_action") == "register"])
        logged_in = len([x for x in items if x.get("auth_action") != "register"])
        skipped_phone = len([x for x in items if x.get("phone_skipped_reason")])
        imported = len([x for x in items if x.get("sub2api")])
        status = "succeeded" if success else "failed"
        summary = {"success": success, "failed": len(errors), "registered": registered, "logged_in": logged_in, "skipped_phone": skipped_phone, "imported": imported, "stage": stage, "errors": errors, "items": items}
        db.update_task(status=status, error="; ".join(errors[:3]) if not success else "", result_json=json.dumps(summary, ensure_ascii=False), finished_at=now_sql())
        db.event(f"注册任务总结：成功 {success}，失败 {len(errors)}，新注册 {registered}，登录更新 {logged_in}，跳过接码 {skipped_phone}，导入反代 {imported}", "info" if success else "error", detail={"scope": "global", **summary})
    except Exception as exc:
        if _is_cancel_exception(exc):
            db.mark_cancelled("用户已中断注册任务")
            return
        db.update_task(status="failed", error=f"SunnyRegister Worker failed: {exc}", result_json=json.dumps({"traceback": traceback.format_exc()[-4000:]}, ensure_ascii=False), finished_at=now_sql())
        db.event(f"SunnyRegister Worker failed: {exc}", "error", detail={"traceback": traceback.format_exc()[-4000:]})
    finally:
        db.close()


