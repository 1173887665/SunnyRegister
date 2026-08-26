from __future__ import annotations

from typing import Any, Callable

import requests


def _bool_config(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "disabled"}


def retain_failed_mailbox(cfg: dict[str, Any]) -> bool:
    return _bool_config(cfg.get("retain_failed_mailboxes"), True)


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error") or payload.get("detail")
        code = payload.get("code")
        if message or code is not None:
            return f"code={code!s} {str(message or '').strip()}".strip()
    text = " ".join(str(response.text or "").split())
    return text[:240] or f"HTTP {response.status_code}"


def _response_success(response: requests.Response) -> bool:
    if not response.ok:
        return False
    try:
        payload = response.json()
    except Exception:
        return True
    if not isinstance(payload, dict):
        return True
    code = payload.get("code")
    return code in (None, "", 0, "0", 200, "200") or payload.get("success") is True


def delete_cloudmail_user(cfg: dict[str, Any], email: str, *, timeout: float = 30) -> None:
    """Delete one CloudMail user through compatible public deletion routes."""
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    token = str(cfg.get("auth_token") or "").strip()
    site_password = str(cfg.get("site_password") or "").strip()
    if not base or not token:
        raise RuntimeError("CloudMail 删除失败：缺少 API 地址或 PUBLIC_API_TOKEN")
    headers = {
        "Accept": "application/json",
        "Authorization": token,
        "X-Auth-Token": token,
        "User-Agent": "SunnyRegister/1.0",
    }
    if site_password:
        headers["x-custom-auth"] = site_password
    configured_path = str(cfg.get("delete_api_path") or "").strip()
    paths = [configured_path] if configured_path else ["/api/public/deleteUser"]
    payloads = (
        {"email": email},
        {"emails": [email]},
        {"list": [email]},
        {"list": [{"email": email}]},
    )
    last_error = "CloudMail 未提供兼容的删除接口"
    for path in paths:
        if not path.startswith("/"):
            path = "/" + path
        for method in ("DELETE", "POST"):
            for payload in payloads:
                try:
                    response = requests.request(
                        method,
                        base + path,
                        params={"email": email} if method == "DELETE" else None,
                        json=payload,
                        headers=headers,
                        timeout=timeout,
                    )
                except requests.RequestException as exc:
                    last_error = str(exc)
                    continue
                if _response_success(response):
                    return
                last_error = f"{path} {method} HTTP {response.status_code}: {_response_error(response)}"
                if response.status_code not in {404, 405, 501}:
                    raise RuntimeError(f"CloudMail 删除邮箱 {email} 失败：{last_error}")
    if "HTTP 404" in last_error or "HTTP 405" in last_error or "HTTP 501" in last_error:
        raise RuntimeError(
            f"CloudMail 删除邮箱 {email} 失败：当前服务未提供可用的 {paths[0]} 删除扩展；"
            "请在 CloudMail 部署该公开删除接口后重试"
        )
    raise RuntimeError(f"CloudMail 删除邮箱 {email} 失败：{last_error}")


def cleanup_failed_mailbox(
    db: Any,
    cfg: dict[str, Any],
    email: str,
    pickup_token_hash: str,
    log: Callable[[str], None],
) -> bool:
    if retain_failed_mailbox(cfg):
        log(f"[{email}] 任务失败，按配置保留域名邮箱")
        return False
    provider_error: Exception | None = None
    try:
        delete_cloudmail_user(cfg, email)
    except Exception as exc:
        provider_error = exc

    # Disabling retention is authoritative for SunnyRegister. Never leave the
    # generated credential visible in the local pool because provider cleanup
    # is unavailable or temporarily failing.
    removed = db.delete_failed_domain_mailbox(email, pickup_token_hash)
    if not removed:
        local_error = RuntimeError("本地未找到匹配的失败域名邮箱记录")
        if provider_error is not None:
            raise RuntimeError(f"{provider_error}；{local_error}") from provider_error
        raise local_error
    if provider_error is not None:
        log(f"[{email}] 已删除本地失败域名邮箱记录，但 CloudMail 邮箱仍可能残留：{provider_error}")
        raise provider_error
    log(f"[{email}] 已删除 CloudMail 邮箱及本地失败域名邮箱记录")
    return True
