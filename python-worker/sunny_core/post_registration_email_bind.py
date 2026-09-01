from __future__ import annotations

from typing import Any, Callable

from .db import SunnyDB


ENABLED_FIELD = "bind_email_after_registration"
TARGETS_FIELD = "bind_target_mailboxes"


def email_bind_requested(payload: dict[str, Any]) -> bool:
    """Return whether the SunnyRegister phone task requested email binding."""
    return payload.get(ENABLED_FIELD) is True


def _target_for_index(payload: dict[str, Any], index: int) -> dict[str, Any] | None:
    targets = payload.get(TARGETS_FIELD)
    if not isinstance(targets, list) or not targets:
        return None
    candidate = targets[(max(1, int(index)) - 1) % len(targets)]
    if not isinstance(candidate, dict):
        return None
    email = str(candidate.get("email") or "").strip()
    mailbox_api = str(candidate.get("mailbox_api") or "").strip()
    if not email or not mailbox_api:
        return None
    return {
        "email": email,
        "mailbox_api": mailbox_api,
        "mailbox_type": str(candidate.get("mailbox_type") or "").strip().lower(),
        "mailbox_channel": str(candidate.get("mailbox_channel") or "").strip().lower(),
    }


def run_post_registration_email_bind(
    db: SunnyDB,
    payload: dict[str, Any],
    *,
    account_id: int,
    source_email: str,
    index: int,
    bind_with_proxy_rotation: Callable[[dict[str, Any], dict[str, Any], int], dict[str, Any]],
    emit_progress: Callable[[str, str, str], None],
) -> dict[str, Any]:
    """Execute the email-binding phase owned by SunnyRegister phone registration.

    The public task contract is deliberately independent from the generic
    ``sunny_rebind`` payload. The adapter fields below are kept private to this
    module so other projects and manual rebind jobs remain unchanged.
    """
    target = _target_for_index(payload, index)
    emit_progress("email_bind_started", "running", "")
    if not target:
        error = "没有可分配的注册后绑定邮箱"
        db.event(
            f"[{source_email}] [绑邮] 注册已完成，但没有可分配的目标邮箱",
            "error",
            detail={"email": source_email, "scope": "selected", "operation": "post_registration_email_bind"},
        )
        emit_progress("email_bind_completed", "abnormal", error)
        return {"email_bind_complete": False, "email_bind_error": error, "stage_error": error}

    target_email = target["email"]
    db.event(
        f"[{source_email}] [绑邮] 注册完成，开始绑定邮箱 {target_email}",
        detail={
            "email": source_email,
            "scope": "selected",
            "operation": "post_registration_email_bind",
            "target_mailbox_type": target["mailbox_type"],
            "target_mailbox_channel": target["mailbox_channel"],
        },
    )
    try:
        account_rows = db.fetch_accounts([account_id])
        if not account_rows:
            raise RuntimeError("注册账户记录不存在")
        account_for_bind = dict(account_rows[0])
        account_for_bind.update(
            {
                "_rebind_target_email": target_email,
                "_rebind_target_api": target["mailbox_api"],
                "_rebind_target_type": target["mailbox_type"],
                "_rebind_target_channel": target["mailbox_channel"],
            }
        )
        bind_payload = dict(payload)
        bind_payload.update(
            {
                "rebind_source": "imported",
                "target_email": target_email,
                "target_mailbox_api": target["mailbox_api"],
                "target_mailbox_type": target["mailbox_type"],
                "target_mailbox_channel": target["mailbox_channel"],
            }
        )
        bind_result = bind_with_proxy_rotation(bind_payload, account_for_bind, max(0, int(index) - 1))
        bound_email = str(bind_result.get("new_email") or target_email)
        db.event(
            f"[{source_email}] [绑邮] 注册后邮箱绑定完成：{bound_email}",
            detail={"email": source_email, "scope": "selected", "operation": "post_registration_email_bind", "email_bind_complete": True},
        )
        emit_progress("email_bind_completed", "completed", "")
        return {"email_bind": bind_result, "email_bind_complete": True}
    except Exception as exc:
        error = str(exc)
        db.event(
            f"[{source_email}] [绑邮] 注册已完成，但注册后绑定邮箱失败，已保留账号结果：{error}",
            "error",
            detail={"email": source_email, "scope": "selected", "operation": "post_registration_email_bind", "email_bind_complete": False},
        )
        emit_progress("email_bind_completed", "abnormal", error)
        return {"email_bind_complete": False, "email_bind_error": error, "stage_error": error}
