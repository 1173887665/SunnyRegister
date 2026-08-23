import json

from sunny_core.mailbox import DomainMailReader, account_from_row


def _credential():
    return json.dumps({"base_url": "https://mail.example", "auth_token": "token-1"})


def test_account_from_row_supports_domain_credentials():
    account = account_from_row({
        "email": "user@example.com",
        "mailbox_type": "domain",
        "mailbox_channel": "domain_api",
        "access_key": _credential(),
    })
    assert account.mailbox_type == "domain"
    assert account.mailbox_channel == "domain_api"
    assert json.loads(account.access_key)["auth_token"] == "token-1"


def test_domain_reader_uses_latest_message_and_extracts_code(monkeypatch):
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": _credential()}),
        None,
    )
    monkeypatch.setattr(reader, "_request", lambda: {"items": [
        {"id": 1, "receivedAt": "2020-01-01T00:00:00Z", "verificationCode": "111111"},
        {"id": 2, "receivedAt": "2099-01-01T00:00:00Z", "bodyPreview": "ChatGPT code 978744"},
    ]})
    current = reader._latest()
    assert current["code"] == "978744"
    assert current["id"] == 2


def test_domain_reader_filters_old_message(monkeypatch):
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": _credential()}),
        None,
    )
    monkeypatch.setattr(reader, "_request", lambda: {"items": [
        {"id": 1, "receivedAt": "2020-01-01T00:00:00Z", "verificationCode": "111111"},
    ]})
    try:
        reader.wait_for_code(2000000000, timeout=0.05)
    except TimeoutError:
        pass
    else:
        raise AssertionError("old domain mailbox message must not satisfy a newer baseline")
