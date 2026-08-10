from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sunny_core import worker
from sunny_core.firefox_sms import FireFoxActivation, FireFoxSMSClient


def firefox_config() -> dict:
    return {
        "firefox_api_token": "stable-token_3",
        "firefox_default_country": "usa",
        "firefox_default_service": "1096",
        "firefox_max_price": 0.65,
    }


def test_get_number_enforces_country_service_max_price_and_one_number() -> None:
    client = FireFoxSMSClient(firefox_config())
    client._authorized = MagicMock(return_value=["1", "PKEY", "time", "usa", "1", "US", "", "2025550101", "DOCK"])

    activation = client.get_number()

    assert activation == FireFoxActivation("PKEY", "+12025550101", "usa", "1", "US", "", "DOCK")
    _, kwargs = client._authorized.call_args
    assert kwargs["iid"] == "1096"
    assert kwargs["country"] == "usa"
    assert kwargs["maxPrice"] == "0.65"
    assert kwargs["otpmode"] == "sms"
    assert "mobile" not in kwargs
    assert "quantity" not in kwargs


def test_get_number_accepts_documented_eight_field_response() -> None:
    client = FireFoxSMSClient({**firefox_config(), "firefox_default_country": "mys"})
    client._authorized = MagicMock(return_value=[
        "1",
        "99A05D8A2BA7850731A981D82E9E968493115CB67959B65A",
        "2026-08-10T08:28:21",
        "mys",
        "60",
        "8960153022305590842f-502153000300069",
        "COM31",
        "1159137308",
    ])

    activation = client.get_number()

    assert activation == FireFoxActivation(
        pkey="99A05D8A2BA7850731A981D82E9E968493115CB67959B65A",
        number="+601159137308",
        country="mys",
        country_code="60",
        location="8960153022305590842f-502153000300069",
        port="COM31",
        dock="",
    )


def test_documented_get_phone_code_and_release_requests_use_pkey() -> None:
    client = FireFoxSMSClient(firefox_config())
    responses = ["0|-3", "1|123456|Your OpenAI code is 123456", "1|"]
    requests_seen: list[dict] = []

    def fake_get(_url, **kwargs):
        requests_seen.append(kwargs["params"])
        response = MagicMock()
        response.text = responses.pop(0)
        response.raise_for_status.return_value = None
        return response

    with patch("sunny_core.firefox_sms.requests.get", side_effect=fake_get):
        assert client.get_code("PKEY") is None
        assert client.get_code("PKEY") == "123456"
        client.release("PKEY")

    assert requests_seen == [
        {"act": "getPhoneCode", "token": "stable-token_3", "pkey": "PKEY"},
        {"act": "getPhoneCode", "token": "stable-token_3", "pkey": "PKEY"},
        {"act": "setRel", "token": "stable-token_3", "pkey": "PKEY"},
    ]


def test_configured_token_is_used_directly_and_root_url_is_normalized() -> None:
    client = FireFoxSMSClient({**firefox_config(), "firefox_base_url": "https://www.firefox.fun/"})
    with patch("sunny_core.firefox_sms.requests.get") as request:
        assert client.api_token == "stable-token_3"
    assert client.base_url == "https://www.firefox.fun/yhapi.ashx"
    request.assert_not_called()


def test_balance_uses_token_once_and_reports_invalid_token() -> None:
    client = FireFoxSMSClient(firefox_config())
    client._request_raw = MagicMock(return_value=(False, ["0", "-2"], "0|-2"))

    with pytest.raises(RuntimeError, match="token is invalid"):
        client.balance()
    client._request_raw.assert_called_once_with("myInfo", timeout=30, token="stable-token_3")


def test_legacy_password_field_is_migrated_as_api_token() -> None:
    config = firefox_config()
    config.pop("firefox_api_token")
    config["firefox_password"] = "legacy-token_3"

    assert FireFoxSMSClient(config).api_token == "legacy-token_3"


def test_get_code_accepts_only_independent_six_digits() -> None:
    client = FireFoxSMSClient(firefox_config())
    client._authorized_raw = MagicMock(return_value=(True, ["1", "", "OpenAI code: 123456"], "1||OpenAI code: 123456"))
    assert client.get_code("PKEY") == "123456"

    client._authorized_raw = MagicMock(return_value=(True, ["1", "12345", "code 12345"], "1|12345|code 12345"))
    with pytest.raises(RuntimeError, match="6-digit"):
        client.get_code("PKEY")


def test_get_phone_reports_country_error_from_official_action_table() -> None:
    client = FireFoxSMSClient(firefox_config())
    client._authorized_raw = MagicMock(return_value=(False, ["0", "-4"], "0|-4"))
    with pytest.raises(RuntimeError, match="country code is invalid"):
        client.get_number()


def test_release_waits_for_provider_delay_and_retries() -> None:
    client = FireFoxSMSClient(firefox_config())
    client._authorized_raw = MagicMock(side_effect=[
        (False, ["0", "2"], "0|2"),
        (True, ["1", ""], "1|"),
    ])
    with patch("sunny_core.firefox_sms.time.sleep") as sleep:
        client.release("PKEY", max_attempts=2)
    sleep.assert_called_once_with(2)
    assert client._authorized_raw.call_count == 2


def test_wait_code_polls_every_five_seconds() -> None:
    client = FireFoxSMSClient(firefox_config())
    client.get_code = MagicMock(side_effect=[None, None, "123456"])
    with patch("sunny_core.firefox_sms.time.sleep") as sleep:
        code = client.wait_code("PKEY", timeout=30)
    assert code == "123456"
    assert sleep.call_count == 2
    assert all(call.args[0] == 5 for call in sleep.call_args_list)


def test_release_later_waits_before_releasing() -> None:
    client = FireFoxSMSClient(firefox_config())
    client.release = MagicMock()
    with patch("sunny_core.firefox_sms.time.sleep") as sleep:
        thread = client.release_later("PKEY", delay=35)
        thread.join(timeout=1)
    sleep.assert_called_once_with(35)
    client.release.assert_called_once_with("PKEY")


class FakeDB:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def get_config(self, key: str) -> dict:
        assert key == "phone"
        return firefox_config()

    def resolve_sms_provider_option(self, *_args):
        return None

    def record_sms_provider_number(self, *_args, **_kwargs) -> None:
        return None

    def mark_sms_provider_number_error(self, *_args, **_kwargs) -> None:
        return None

    def mark_sms_provider_number_success(self, *_args, **_kwargs) -> None:
        return None

    def event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


def test_worker_retries_firefox_three_times_and_schedules_release() -> None:
    db = FakeDB()
    client = MagicMock()
    client.country = "usa"
    client.service = "1096"
    client.max_price = 0.65
    client.get_number.side_effect = [
        FireFoxActivation("P1", "+12025550101"),
        FireFoxActivation("P2", "+12025550102"),
        FireFoxActivation("P3", "+12025550103"),
    ]

    with patch.object(worker, "FireFoxSMSClient", return_value=client):
        provider = worker._firefox_provider(db, "user@example.com")

    first = provider("next", "user@example.com")
    assert provider("bad", "user@example.com", first) == {"retry_same_provider": True}
    second = provider("next", "user@example.com")
    assert provider("bad", "user@example.com", second) == {"retry_same_provider": True}
    third = provider("next", "user@example.com")
    assert provider("bad", "user@example.com", third) == {"retry_same_provider": False}
    assert provider("next", "user@example.com") is None
    assert client.release_later.call_count == 3
    assert [call.args for call in client.release_later.call_args_list] == [("P1", 35), ("P2", 35), ("P3", 35)]
