import json

from sunny_core.mailbox import RemailReader, account_from_row


def test_account_from_row_supports_remail_json_credentials():
    credential = json.dumps({"base_url": "https://remail.example", "api_key": "k", "order_no": "R1", "service_token": "st"})
    account = account_from_row({"email": "user@example.com", "mailbox_type": "remail", "mailbox_channel": "remail_api", "access_key": credential})
    assert account.mailbox_type == "remail"
    assert account.mailbox_channel == "remail_api"
    assert account.access_key == credential


def test_remail_reader_extracts_fresh_code(monkeypatch):
    credential = json.dumps({"base_url": "https://remail.example", "api_key": "k", "order_no": "R1", "service_token": "st"})
    reader = RemailReader(account_from_row({"email": "user@example.com", "mailbox_type": "remail", "access_key": credential}), None)
    monkeypatch.setattr(reader, "_request", lambda path, params=None: {"verificationCode": "978744", "lastMailReceivedAt": "2099-01-01T00:00:00Z", "id": "m1"})
    assert reader.wait_for_code(0, timeout=1) == "978744"


def test_remail_reader_uses_pickup_url_and_latest_item(monkeypatch):
    pickup = "https://remail.example/v1/pickup?email=user@example.com&token=st-1"
    reader = RemailReader(account_from_row({"email": "user@example.com", "mailbox_type": "remail", "mailbox_channel": "remail_api", "access_key": pickup}), None)
    calls = []

    def request(path, params=None):
        calls.append((path, params))
        return {"items": [
            {"id": 2, "receivedAt": "2099-01-01T00:00:00Z", "verificationCode": "323090", "bodyPreview": "new"},
            {"id": 1, "receivedAt": "2020-01-01T00:00:00Z", "verificationCode": "111111", "bodyPreview": "old"},
        ]}

    monkeypatch.setattr(reader, "_request", request)
    assert reader.wait_for_code(0, timeout=1) == "323090"
    assert calls == [("/v1/pickup", {"email": "user@example.com", "token": "st-1"})]


def test_remail_reader_filters_messages_before_baseline(monkeypatch):
    pickup = "https://remail.example/v1/pickup?email=user@example.com&token=st-1"
    reader = RemailReader(account_from_row({"email": "user@example.com", "mailbox_type": "remail", "access_key": pickup}), None)
    monkeypatch.setattr(reader, "_request", lambda path, params=None: {"items": [{"id": 1, "receivedAt": "2020-01-01T00:00:00Z", "verificationCode": "111111"}]})
    try:
        reader.wait_for_code(2000000000, timeout=0.05)
    except TimeoutError:
        pass
    else:
        raise AssertionError("old Remail message must not satisfy a newer baseline")
