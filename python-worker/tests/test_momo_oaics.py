from __future__ import annotations

import sys
from pathlib import Path


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402


def test_momo_stage1_defers_trial_campaign_until_oaics_update() -> None:
    payload = checkout_app.checkout_payload(
        {
            "plan": "plus",
            "link_type": "momo",
            "country": "VN",
            "currency": "VND",
            "checkout_country": "VN",
            "checkout_currency": "VND",
            "use_promo": True,
            "promo_campaign": "plus-1-month-free",
            "promo_on_create": False,
        },
        {},
    )

    assert payload["checkout_ui_mode"] == "custom"
    assert payload["billing_details"] == {"country": "VN", "currency": "VND"}
    assert "promo_campaign" not in payload
