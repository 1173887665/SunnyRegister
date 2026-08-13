from __future__ import annotations

from unittest.mock import MagicMock, patch

from sunny_core import worker
from sunny_core.smspool import SMSPOOL_CODE_TIMEOUT_SECONDS, SMSPoolActivation, SMSPoolClient, SMSPoolReusableOrder


class FakeDB:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.errors: list[tuple[str, str, str]] = []
        self.records: list[tuple] = []

    def get_config(self, key: str) -> dict:
        assert key == "phone"
        return {
            "smspool_api_key": "test-key",
            "smspool_default_country": "1",
            "smspool_default_service": "671",
        }

    def resolve_sms_provider_option(self, *_args):
        return None

    def sms_provider_option_extra(self, _option):
        return {}

    def event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))

    def mark_sms_provider_number_error(self, provider: str, number: str, error: str) -> None:
        self.errors.append((provider, number, error))

    def record_sms_provider_number(self, *args, **kwargs) -> None:
        self.records.append((args, kwargs))

    def mark_sms_provider_number_success(self, *_args, **_kwargs) -> None:
        return None


def test_latest_reusable_order_uses_largest_orders_new_id() -> None:
    client = SMSPoolClient({"smspool_api_key": "test-key"})
    client._post = MagicMock(
        return_value={
            "orders_new": [
                {"id": 8, "phonenumber": "12025550108", "order_code": "OLD"},
                {"id": 42, "phonenumber": "12025550142", "order_code": "LATEST"},
                {"id": 17, "phonenumber": "12025550117", "order_code": "MIDDLE"},
            ]
        }
    )

    order = client.latest_reusable_order()

    assert order == SMSPoolReusableOrder(id=42, number="+12025550142", order_id="LATEST")
    client._post.assert_called_once_with("/request/orders_new", {}, timeout=20)


def test_smspool_reuse_failure_then_limits_new_numbers_to_three() -> None:
    db = FakeDB()
    client = MagicMock()
    client.country = "1"
    client.service = "671"
    client.pool = ""
    client.max_price = -1
    client.latest_reusable_order.return_value = SMSPoolReusableOrder(id=99, number="+12025550099")
    client.get_number.side_effect = [
        RuntimeError("reuse unavailable"),
        SMSPoolActivation("NEW1", "+12025550101"),
        SMSPoolActivation("NEW2", "+12025550102"),
        SMSPoolActivation("NEW3", "+12025550103"),
    ]
    client.wait_code.return_value = "123456"

    with patch.object(worker, "SMSPoolClient", return_value=client):
        provider = worker._smspool_provider(db, "user@example.com")

    first = provider("next", "user@example.com")
    assert first["number"] == "+12025550101"
    assert first["new_number_attempt"] == 1
    assert provider("code", "user@example.com", first) == "123456"
    assert provider("bad", "user@example.com", first) == {"retry_same_provider": True}

    second = provider("next", "user@example.com")
    assert second["new_number_attempt"] == 2
    assert provider("bad", "user@example.com", second) == {"retry_same_provider": True}

    third = provider("next", "user@example.com")
    assert third["new_number_attempt"] == 3
    assert provider("bad", "user@example.com", third) == {"retry_same_provider": False}
    assert provider("next", "user@example.com") is None

    assert client.get_number.call_count == 4
    client.get_number.assert_any_call(preferred_number="+12025550099")
    assert client.cancel.call_count == 3
    client.wait_code.assert_called_once_with("NEW1", timeout=SMSPOOL_CODE_TIMEOUT_SECONDS, log=client.wait_code.call_args.kwargs["log"])


def test_combined_provider_keeps_smspool_until_its_retry_budget_is_exhausted() -> None:
    db = MagicMock()
    db.smsbower_available.return_value = False
    db.smspool_available.return_value = True
    db.usable_phone_count.return_value = 0
    smspool_provider = MagicMock()
    smspool_provider.side_effect = [
        {"provider": "smspool", "number": "+12025550101"},
        {"retry_same_provider": True},
        {"provider": "smspool", "number": "+12025550102"},
    ]

    with patch.object(worker, "_smspool_provider", return_value=smspool_provider):
        provider = worker._combined_phone_provider(db, "user@example.com")
        first = provider("next", "user@example.com")
        provider("bad", "user@example.com", first)
        second = provider("next", "user@example.com")

    assert first["number"] == "+12025550101"
    assert second["number"] == "+12025550102"
    assert [call.args[0] for call in smspool_provider.call_args_list] == ["next", "bad", "next"]


def test_background_combined_provider_temporarily_forces_us_country() -> None:
    db = MagicMock()
    db.smsbower_available.return_value = False
    db.smspool_available.return_value = True
    db.firefox_available.return_value = False
    db.luban_available.return_value = False
    db.usable_phone_count.return_value = 0
    smspool_provider = MagicMock(return_value={"provider": "smspool", "number": "+12025550101", "country_code": "1"})

    with patch.object(worker, "_smspool_provider", return_value=smspool_provider) as factory:
        provider = worker._combined_phone_provider(db, "user@example.com", execution_mode="background")
        phone = provider("next", "user@example.com")

    assert phone["number"].startswith("+1")
    factory.assert_called_once_with(db, "user@example.com", "", "1")


def test_protocol_combined_provider_keeps_saved_provider_country() -> None:
    db = MagicMock()
    db.smsbower_available.return_value = False
    db.smspool_available.return_value = False
    db.firefox_available.return_value = True
    db.luban_available.return_value = False
    db.usable_phone_count.return_value = 0
    firefox_provider = MagicMock(return_value={"provider": "firefox", "number": "+601137984883", "country": "mys", "country_code": "60"})

    with patch.object(worker, "_firefox_provider", return_value=firefox_provider) as factory:
        provider = worker._combined_phone_provider(db, "user@example.com", execution_mode="protocol")
        phone = provider("next", "user@example.com")

    assert phone["country"] == "mys"
    assert phone["country_code"] == "60"
    factory.assert_called_once_with(db, "user@example.com", "", "")


def test_background_combined_provider_releases_non_us_number() -> None:
    db = MagicMock()
    db.smsbower_available.return_value = False
    db.smspool_available.return_value = True
    db.firefox_available.return_value = False
    db.luban_available.return_value = False
    db.usable_phone_count.return_value = 0
    smspool_provider = MagicMock()
    smspool_provider.side_effect = [
        {"provider": "smspool", "number": "+601137984883", "country_code": "60"},
        {"retry_same_provider": False},
    ]

    with patch.object(worker, "_smspool_provider", return_value=smspool_provider):
        provider = worker._combined_phone_provider(db, "user@example.com", execution_mode="background")
        assert provider("next", "user@example.com") is None
        assert provider("next", "user@example.com") is None

    assert [call.args[0] for call in smspool_provider.call_args_list] == ["next", "bad"]
