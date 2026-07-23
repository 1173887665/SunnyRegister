from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_der_private_key,
)
from curl_cffi import requests as curl_requests


AUTH_API_BASE = "https://auth.openai.com/api/accounts"
AUTH_MODE = "agentIdentity"
AGENT_VERSION = "standalone-script-1"
AGENT_HARNESS_ID = "sunnyregister"
RUNNING_LOCATION = "custom-python"


def decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise ValueError("Access Token 不是有效的 JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception as exc:
        raise ValueError("Access Token JWT payload 解析失败") from exc
    if not isinstance(value, dict):
        raise ValueError("Access Token JWT payload 格式无效")
    return value


def generate_ed25519_keypair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_der = private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_type = b"ssh-ed25519"
    ssh_blob = (
        len(key_type).to_bytes(4, "big")
        + key_type
        + len(public_raw).to_bytes(4, "big")
        + public_raw
    )
    return (
        base64.b64encode(private_der).decode("ascii"),
        f"ssh-ed25519 {base64.b64encode(ssh_blob).decode('ascii')}",
    )


def _check_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        from .openai_auth import TaskCancelledError

        raise TaskCancelledError("Task cancelled by user")


def _session(proxy_url: str):
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    return curl_requests.Session(impersonate="chrome136", proxies=proxies, timeout=30)


def _response_error(response, action: str) -> RuntimeError:
    body = str(getattr(response, "text", "") or "")[:500]
    lowered = body.lower()
    if "unsupported_country_region_territory" in lowered:
        return RuntimeError(f"{action}失败: 当前网络出口所在地区不受 OpenAI 支持，请检查注册代理（HTTP 403）")
    if "just a moment" in lowered or "challenges.cloudflare.com" in lowered:
        return RuntimeError(f"{action}失败: 当前网络出口触发 Cloudflare 浏览器挑战，请更换健康代理后重试（HTTP 403）")
    return RuntimeError(f"{action}失败: HTTP {getattr(response, 'status_code', 0)} {body}")


def _post_with_retry(
    client,
    url: str,
    *,
    action: str,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    **kwargs,
):
    response = None
    for attempt in range(3):
        _check_cancelled(should_cancel)
        try:
            response = client.post(url, **kwargs)
        except Exception:
            if attempt >= 2:
                raise
            if log:
                log(f"[反代] {action}网络请求失败，正在重试 {attempt + 1}/2")
            time.sleep(1.5 * (attempt + 1))
            continue
        status = int(getattr(response, "status_code", 0) or 0)
        if status < 500 and status != 429:
            return response
        if attempt < 2:
            if log:
                log(f"[反代] {action}暂时不可用，正在重试 {attempt + 1}/2（HTTP {status}）")
            time.sleep(1.5 * (attempt + 1))
    return response


def create_agent_identity_auth(
    access_token: str,
    *,
    email: str = "",
    plan_type: str = "",
    proxy_url: str = "",
    should_cancel: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create an in-memory Codex auth.json record without logging secrets."""
    _check_cancelled(should_cancel)
    claims = decode_jwt_claims(access_token)
    expires_at = int(claims.get("exp") or 0)
    if expires_at and expires_at <= int(time.time()) + 30:
        raise RuntimeError("Access Token 已过期，无法创建 Agent Identity")
    auth_claims = claims.get("https://api.openai.com/auth")
    profile_claims = claims.get("https://api.openai.com/profile")
    auth_claims = auth_claims if isinstance(auth_claims, dict) else {}
    profile_claims = profile_claims if isinstance(profile_claims, dict) else {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()
    user_id = str(auth_claims.get("chatgpt_user_id") or auth_claims.get("user_id") or "").strip()
    resolved_email = str(profile_claims.get("email") or claims.get("email") or email or "").strip()
    resolved_plan = str(auth_claims.get("chatgpt_plan_type") or plan_type or "free").strip().lower()
    if not account_id or not user_id:
        raise RuntimeError("Access Token 缺少 Agent Identity 所需的 account_id 或 chatgpt_user_id")

    private_key, public_key = generate_ed25519_keypair()
    client = _session(proxy_url)
    try:
        if log:
            log("[反代] 正在创建 Codex Agent Identity")
        response = _post_with_retry(
            client,
            f"{AUTH_API_BASE}/v1/agent/register",
            action="Agent Identity 注册",
            log=log,
            should_cancel=should_cancel,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={
                "abom": {
                    "agent_version": AGENT_VERSION,
                    "agent_harness_id": AGENT_HARNESS_ID,
                    "running_location": RUNNING_LOCATION,
                },
                "agent_public_key": public_key,
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise _response_error(response, "Agent Identity 注册")
        data = response.json()
        runtime_id = str(data.get("agent_runtime_id") or "").strip()
        if not runtime_id:
            raise RuntimeError("Agent Identity 注册结果缺少 agent_runtime_id")

        _check_cancelled(should_cancel)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        private_obj = load_der_private_key(base64.b64decode(private_key), password=None)
        if not isinstance(private_obj, Ed25519PrivateKey):
            raise RuntimeError("Agent Identity 私钥类型无效")
        signature = base64.b64encode(private_obj.sign(f"{runtime_id}:{timestamp}".encode("utf-8"))).decode("ascii")
        task_id = ""
        try:
            task_response = _post_with_retry(
                client,
                f"{AUTH_API_BASE}/v1/agent/{runtime_id}/task/register",
                action="Agent task 预注册",
                log=log,
                should_cancel=should_cancel,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"timestamp": timestamp, "signature": signature},
                timeout=30,
            )
            if task_response.status_code == 200:
                task_id = str(task_response.json().get("encrypted_task_id") or "").strip()
            elif log:
                log(f"[反代] Agent task 预注册未完成，Sub2API 将在首次请求时重建: HTTP {task_response.status_code}")
        except Exception as exc:
            if log:
                log(f"[反代] Agent task 预注册未完成，Sub2API 将在首次请求时重建: {exc}")
        _check_cancelled(should_cancel)

        identity = {
            "agent_runtime_id": runtime_id,
            "agent_private_key": private_key,
            "account_id": account_id,
            "chatgpt_user_id": user_id,
            "email": resolved_email,
            "plan_type": resolved_plan,
            "chatgpt_account_is_fedramp": bool(auth_claims.get("chatgpt_account_is_fedramp", False)),
        }
        if task_id:
            identity["task_id"] = task_id
        return {"auth_mode": AUTH_MODE, "agent_identity": identity}
    finally:
        try:
            client.close()
        except Exception:
            pass
