from __future__ import annotations


def checkout_mode(checkout_kind: str) -> str:
    normalized = str(checkout_kind or "unknown").strip().lower()
    if normalized == "oaics":
        return "oaics"
    if normalized in {"cs_live", "cs_test"}:
        return "cs_live"
    return "auto"


def mode_for_session_kind(checkout_kind: str) -> str:
    normalized = str(checkout_kind or "unknown").strip().lower()
    if normalized == "oaics":
        return "oaics"
    if normalized in {"cs_live", "cs_test"}:
        return "cs_live"
    return "auto"


def reconcile_checkout_mode(expected_mode: str, actual_kind: str) -> tuple[str, bool]:
    actual_mode = mode_for_session_kind(actual_kind)
    expected = str(expected_mode or "auto").strip().lower()
    mismatch = expected != "auto" and actual_mode != "auto" and expected != actual_mode
    return actual_mode, mismatch


def session_checkout_kind(session_id: str) -> str:
    normalized = str(session_id or "").strip().lower()
    if normalized.startswith("oaics_"):
        return "oaics"
    if normalized.startswith("cs_live_"):
        return "cs_live"
    if normalized.startswith("cs_test_"):
        return "cs_test"
    return "unknown"


def validate_session_for_mode(mode: str, session_id: str) -> str:
    actual = session_checkout_kind(session_id)
    if mode == "oaics" and actual != "oaics":
        raise ValueError(f"OAICS account returned {actual} checkout")
    if mode == "cs_live" and actual == "oaics":
        raise ValueError("CS Live account returned OAICS checkout")
    return actual
