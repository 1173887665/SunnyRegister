from __future__ import annotations

import sys
from pathlib import Path

import pytest


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402


@pytest.mark.parametrize(
    ("promo_requested", "campaign", "requires_zero"),
    [
        (False, "plus-1-month-free", False),
        (True, "plus-1-month-free", True),
        (True, "plus-1-month-50-pct-off", False),
        (True, "account-specific-discount", False),
    ],
)
def test_hosted_zero_due_is_required_only_for_free_campaign(
    promo_requested: bool, campaign: str, requires_zero: bool
) -> None:
    assert checkout_app.hosted_requires_zero_due(promo_requested, campaign) is requires_zero


def test_hosted_checkout_payload_follows_promotion_toggle() -> None:
    options = {
        "plan": "plus",
        "link_type": "hosted",
        "country": "PH",
        "currency": "PHP",
        "promo_campaign": "plus-1-month-50-pct-off",
        "use_promo": True,
    }

    discounted = checkout_app.checkout_payload(options.copy(), {})
    original_price = checkout_app.checkout_payload({**options, "use_promo": False}, {})

    assert discounted["promo_campaign"]["promo_campaign_id"] == "plus-1-month-50-pct-off"
    assert "promo_campaign" not in original_price
