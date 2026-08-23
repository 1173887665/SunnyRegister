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
