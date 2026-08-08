from __future__ import annotations

from unittest.mock import MagicMock, patch

from sunny_core import worker
from sunny_core.firefox_sms import FireFoxActivation, FireFoxSMSClient


def firefox_config() -> dict:
    return {
        "firefox_api_name": "api-user",
        "firefox_password": "secret",
        "firefox_default_country": "usa",
        "firefox_default_service": "1096",
        "firefox_max_price": 0.65,
    }


def test_get_number_enforces_country_service_max_price_and_one_number() -> None:
    client = FireFoxSMSClient(firefox_config())
    client._token = "token"
    client._request = MagicMock(return_value=["1", "PKEY", "time", "usa", "1", "US", "", "2025550101", ""])

    activation = client.get_number()

    assert activation == FireFoxActivation("PKEY", "+12025550101", "usa", "1", "US", "")
    _, kwargs = client._request.call_args
    assert kwargs["token"] == "token"
    assert kwargs["iid"] == "1096"
    assert kwargs["country"] == "usa"
    assert kwargs["maxPrice"] == "0.65"
    assert kwargs["otpmode"] == "sms"
    assert "mobile" not in kwargs
    assert "quantity" not in kwargs


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
