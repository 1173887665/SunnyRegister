import pytest

from tools.pay153_checkout.paypal_routing import (
    checkout_mode,
    reconcile_checkout_mode,
    session_checkout_kind,
    validate_session_for_mode,
)


def test_checkout_mode_uses_detected_account_type() -> None:
    assert checkout_mode("oaics") == "oaics"
    assert checkout_mode("cs_live") == "cs_live"
    assert checkout_mode("cs_test") == "cs_live"
    assert checkout_mode("unknown") == "auto"


def test_session_kind_detects_oaics_and_stripe_sessions() -> None:
    assert session_checkout_kind("oaics_123") == "oaics"
    assert session_checkout_kind("cs_live_123") == "cs_live"
    assert session_checkout_kind("cs_test_123") == "cs_test"


def test_known_modes_reject_the_opposite_checkout_type() -> None:
    with pytest.raises(ValueError, match="OAICS account"):
        validate_session_for_mode("oaics", "cs_live_123")
    with pytest.raises(ValueError, match="CS Live account"):
        validate_session_for_mode("cs_live", "oaics_123")


def test_auto_mode_selects_from_created_session() -> None:
    assert validate_session_for_mode("auto", "oaics_123") == "oaics"
    assert validate_session_for_mode("auto", "cs_live_123") == "cs_live"


def test_reconcile_checkout_mode_switches_to_actual_session_kind() -> None:
    assert reconcile_checkout_mode("oaics", "cs_live") == ("cs_live", True)
    assert reconcile_checkout_mode("cs_live", "oaics") == ("oaics", True)
    assert reconcile_checkout_mode("oaics", "oaics") == ("oaics", False)
