from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sunny_core.grizzly_sms import GrizzlySMSActivation, GrizzlySMSClient


def config() -> dict:
    return {
        "grizzlysms_api_key": "test-key",
        "grizzlysms_default_country": "12",
        "grizzlysms_default_service": "dr",
        "grizzlysms_max_price": 0.4,
    }


def response(payload, status: int = 200):
    obj = MagicMock()
    obj.status_code = status
    obj.text = payload if isinstance(payload, str) else "json"
    obj.json.return_value = payload
    return obj


def test_get_number_v2_parses_documented_payload() -> None:
    client = GrizzlySMSClient(config())
    with patch("sunny_core.grizzly_sms.requests.get", return_value=response({
        "activationId": 583924716,
        "phoneNumber": "447537184920",
        "activationCost": 0.4,
        "countryCode": "12",
    })) as request:
        activation = client.get_number()

    assert activation == GrizzlySMSActivation("583924716", "+447537184920", "12", {
        "activationId": 583924716,
        "phoneNumber": "447537184920",
        "activationCost": 0.4,
        "countryCode": "12",
    })
    kwargs = request.call_args.kwargs
    assert kwargs["params"] == {
        "api_key": "test-key",
        "action": "getNumberV2",
        "service": "dr",
        "country": "12",
        "maxPrice": 0.4,
    }


def test_wait_code_reads_nested_sms_code_and_polls() -> None:
    client = GrizzlySMSClient(config())
    client.get_status = MagicMock(side_effect=[{"sms": None, "status": "WAIT_CODE"}, {"sms": {"code": "852508"}}])
    with patch("sunny_core.grizzly_sms.time.sleep") as sleep:
        assert client.wait_code("42", timeout=20) == "852508"
    sleep.assert_called_once()


def test_balance_accepts_legacy_text_response() -> None:
    client = GrizzlySMSClient(config())
    with patch("sunny_core.grizzly_sms.requests.get", return_value=response("ACCESS_BALANCE:3.75")):
        assert client.balance() == "3.75"


def test_wait_code_raises_on_terminal_status() -> None:
    client = GrizzlySMSClient(config())
    client.get_status = MagicMock(return_value={"status": "CANCEL"})
    with pytest.raises(RuntimeError):
        client.wait_code("42", timeout=1)
