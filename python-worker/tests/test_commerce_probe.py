from unittest.mock import MagicMock, patch

from sunny_core.commerce_probe import probe_commerce


def response(status: int, payload=None, content_type: str = "application/json"):
    value = MagicMock()
    value.status_code = status
    value.headers = {"content-type": content_type}
    if payload is None:
        value.json.side_effect = ValueError("not json")
    else:
        value.json.return_value = payload
    return value


def test_probe_commerce_parses_trial_checkout_and_payment_methods() -> None:
    session = MagicMock()
    session.get.return_value = response(200, {"state": "eligible"})
    session.post.return_value = response(
        200,
        {
            "checkout_session_id": "oaics_test",
            "custom_payment_methods": [{"type": "card"}, {"type": "paypal"}, {"type": "card"}],
        },
    )
    with patch("sunny_core.commerce_probe.curl_requests.Session", return_value=session):
        result = probe_commerce("token")

    assert result["trial"] == {"state": "eligible", "http": 200, "error": ""}
    assert result["checkout"]["kind"] == "oaics"
    assert result["checkout"]["payment_methods"] == ["card", "paypal"]


def test_probe_commerce_reports_html_challenge_without_leaking_body() -> None:
    session = MagicMock()
    session.get.return_value = response(403, None, "text/html; charset=UTF-8")
    session.post.return_value = response(403, None, "text/html")
    with patch("sunny_core.commerce_probe.curl_requests.Session", return_value=session):
        result = probe_commerce("token")

    assert result["trial"]["error"] == "HTTP 403 returned text/html content"
    assert result["checkout"]["error"] == "HTTP 403 returned text/html content"


def test_trial_network_failure_does_not_skip_checkout() -> None:
    session = MagicMock()
    session.get.side_effect = ConnectionError("trial interrupted")
    session.post.return_value = response(200, {"checkout_session_id": "cs_live_test"})
    with patch("sunny_core.commerce_probe.curl_requests.Session", return_value=session), patch("sunny_core.commerce_probe.time.sleep"):
        result = probe_commerce("token")

    assert result["trial"]["http"] == 0
    assert "trial interrupted" in result["trial"]["error"]
    assert result["checkout"]["kind"] == "cs_live"
    assert session.get.call_count == 2


def test_probe_commerce_uses_separate_promotion_and_checkout_proxies() -> None:
    promotion_session = MagicMock()
    promotion_session.get.return_value = response(200, {"state": "eligible"})
    checkout_session = MagicMock()
    checkout_session.post.return_value = response(200, {"checkout_session_id": "cs_live_test"})

    with patch(
        "sunny_core.commerce_probe.curl_requests.Session",
        side_effect=[promotion_session, checkout_session],
    ):
        result = probe_commerce(
            "token",
            promotion_proxy_url="http://promotion-proxy",
            checkout_proxy_url="http://checkout-proxy",
        )

    assert result["trial"]["state"] == "eligible"
    assert result["checkout"]["kind"] == "cs_live"
    assert promotion_session.proxies == {
        "http": "http://promotion-proxy",
        "https": "http://promotion-proxy",
    }
    assert checkout_session.proxies == {
        "http": "http://checkout-proxy",
        "https": "http://checkout-proxy",
    }
