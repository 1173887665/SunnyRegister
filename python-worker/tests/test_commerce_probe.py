from contextlib import nullcontext
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
    with (
        patch("sunny_core.commerce_probe.curl_requests.Session", return_value=session),
        patch(
            "sunny_core.commerce_probe._task_style_checkout_probe",
            return_value={"kind": "oaics", "payment_methods": ["card", "paypal"], "http": 200, "error": ""},
        ),
    ):
        result = probe_commerce("token")

    assert result["trial"] == {"state": "eligible", "http": 200, "error": ""}
    assert result["checkout"]["kind"] == "oaics"
    assert result["checkout"]["payment_methods"] == ["card", "paypal"]


def test_probe_commerce_reports_html_challenge_without_leaking_body() -> None:
    session = MagicMock()
    session.get.return_value = response(403, None, "text/html; charset=UTF-8")
    with (
        patch("sunny_core.commerce_probe.curl_requests.Session", return_value=session),
        patch(
            "sunny_core.commerce_probe._task_style_checkout_probe",
            side_effect=RuntimeError("HTTP 403 returned text/html content"),
        ),
    ):
        result = probe_commerce("token")

    assert result["trial"]["error"] == "HTTP 403 returned text/html content"
    assert result["checkout"]["error"] == "RuntimeError: HTTP 403 returned text/html content"


def test_trial_network_failure_does_not_skip_checkout() -> None:
    session = MagicMock()
    session.get.side_effect = ConnectionError("trial interrupted")
    with (
        patch("sunny_core.commerce_probe.curl_requests.Session", return_value=session),
        patch("sunny_core.commerce_probe._task_style_checkout_probe", return_value={"kind": "cs_live", "payment_methods": [], "http": 200, "error": ""}),
        patch("sunny_core.commerce_probe.time.sleep"),
    ):
        result = probe_commerce("token")

    assert result["trial"]["http"] == 0
    assert "trial interrupted" in result["trial"]["error"]
    assert result["checkout"]["kind"] == "cs_live"
    assert session.get.call_count == 2


def test_probe_commerce_uses_separate_promotion_and_checkout_proxies() -> None:
    promotion_session = MagicMock()
    promotion_session.get.return_value = response(200, {"state": "eligible"})
    with (
        patch("sunny_core.commerce_probe.curl_requests.Session", return_value=promotion_session),
        patch("sunny_core.commerce_probe._task_style_checkout_probe", return_value={"kind": "cs_live", "payment_methods": [], "http": 200, "error": ""}) as checkout_probe,
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
    checkout_probe.assert_called_once_with("token", "DE", "EUR", "http://checkout-proxy")
    assert promotion_session.trust_env is False


def test_probe_commerce_returns_worker_proxy_traffic_summary() -> None:
    session = MagicMock()
    session.get.return_value = response(200, {"state": "eligible"})

    class FakeMeter:
        snapshots = iter(({"requests": 2, "total_bytes": 120}, {"requests": 3, "total_bytes": 340}))

        def __init__(self, **_kwargs):
            self.snapshot_value = next(self.snapshots)

        def snapshot(self):
            return self.snapshot_value

    with (
        patch("sunny_core.commerce_probe.curl_requests.Session", return_value=session),
        patch("sunny_core.commerce_probe.ProxyTrafficMeter", side_effect=FakeMeter),
        patch("sunny_core.commerce_probe.use_traffic_meter", side_effect=lambda meter: nullcontext(meter)),
        patch(
            "sunny_core.commerce_probe._task_style_checkout_probe",
            return_value={"kind": "oaics", "payment_methods": [], "http": 200, "error": ""},
        ),
    ):
        result = probe_commerce(
            "token",
            promotion_proxy_url="http://promotion-proxy",
            checkout_proxy_url="http://checkout-proxy",
        )

    assert result["traffic"] == {"requests": 5, "total_bytes": 460}
