from __future__ import annotations

import sys
from pathlib import Path


PAY153_DIR = Path(__file__).parents[1] / "tools" / "pay153_checkout"
if str(PAY153_DIR) not in sys.path:
    sys.path.insert(0, str(PAY153_DIR))

import app as checkout_app  # noqa: E402


def test_gcash_authorization_url_requires_m_gcash_host() -> None:
    assert checkout_app.is_valid_gcash_authorization_url(
        "https://m.gcash.com/gcashapp/gcash-merchants-auth/index.html?siteId=1"
    ) is True
    assert checkout_app.is_valid_gcash_authorization_url(
        "https://checkoutshopper-live.adyen.com/checkoutshopper/checkoutPaymentRedirect?redirectData=x"
    ) is False
    assert checkout_app.is_valid_gcash_authorization_url(
        "https://gcash.com/gcashapp/authorization"
    ) is False


def test_gcash_authorization_url_prefers_nested_final_link() -> None:
    payload = {
        "next_action": {
            "url": "https://checkoutshopper-live.adyen.com/checkoutshopper/checkoutPaymentRedirect?redirectData=x",
        },
        "confirm_return_url": "https://m.gcash.com/gcashapp/gcash-merchants-auth/index.html?merchantId=2188",
    }
    assert checkout_app.gcash_authorization_url(payload) == payload["confirm_return_url"]


def test_gcash_authorization_url_rejects_redirect_only_payload() -> None:
    assert checkout_app.gcash_authorization_url(
        {"next_action": {"url": "https://checkoutshopper-live.adyen.com/checkoutshopper/checkoutPaymentRedirect"}}
    ) == ""
