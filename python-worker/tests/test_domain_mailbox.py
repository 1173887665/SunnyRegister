import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from sunny_core import mailbox as mailbox_module
from sunny_core import rebind as rebind_module
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


def test_domain_reader_prefers_body_code_and_parses_cloudmail_utc(monkeypatch):
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": _credential()}),
        None,
    )
    monkeypatch.setattr(reader, "_request", lambda: {"items": [{
        "id": 3,
        "receivedAt": "2026-08-24 07:34:15",
        "bodyPreview": "<style>.code{content:202123}</style><p>ChatGPT code 876769</p>",
        "verificationCode": "202123",
    }]})
    current = reader._latest()
    assert current["code"] == "876769"
    assert "<" not in current["body"]
    assert current["timestamp"] == datetime(2026, 8, 24, 7, 34, 15, tzinfo=timezone.utc).timestamp()


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


def test_domain_reader_accepts_unix_millisecond_timestamp(monkeypatch):
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": _credential()}),
        None,
    )
    monkeypatch.setattr(reader, "_request", lambda: {"items": [{
        "id": "m1",
        "timestamp": 4102444800000,
        "bodyPreview": "ChatGPT code 978744",
    }]})
    current = reader._latest()
    assert current["code"] == "978744"
    assert current["timestamp"] == 4102444800


def test_domain_reader_uses_individual_pickup_url(monkeypatch):
    pickup_url = "https://sunny.example/api/sunny/domain-mail/pickup?email=user%40example.com&token=dmsk_one"
    logs = []
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": pickup_url}),
        logs.append,
    )

    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return {"items": [{"id": 3, "receivedAt": "2099-01-01T00:00:00Z", "verificationCode": "978744"}]}

        @staticmethod
        def close():
            return None

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(mailbox_module.requests, "get", fake_get)
    assert reader._latest()["code"] == "978744"
    assert calls[0][0] == pickup_url
    assert "Authorization" not in calls[0][1]["headers"]
    assert any("HTTP 200" in message and "识别到 1 封验证码邮件" in message for message in logs)


def test_rebind_domain_mailbox_creates_individual_pickup_credential(monkeypatch):
    class DB:
        @staticmethod
        def get_config(_key):
            return {
                "enabled_for_rebinding": True,
                "base_url": "https://cloudmail.example",
                "auth_token": "global-manager-token",
                "site_password": "site-password",
                "pickup_base_url": "https://sunny.example",
                "domain": "example.com",
                "random_local_length": 10,
            }

    class Response:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"code": 0}

    monkeypatch.setattr(rebind_module.requests, "post", lambda *args, **kwargs: Response())
    logs = []
    email, credential, token_hash = rebind_module._domain_mailbox(DB(), logs.append)
    parsed = urlparse(credential)
    query = parse_qs(parsed.query)
    pickup_token = query["token"][0]
    assert parsed.netloc == "sunny.example"
    assert query["email"] == [email]
    assert pickup_token.startswith("dmsk_")
    assert token_hash == hashlib.sha256(pickup_token.encode("utf-8")).hexdigest()
    assert "global-manager-token" not in credential
    assert any(f"{email}----{credential}" in message for message in logs)


def test_rebind_retries_begin_after_first_otp_delivery_timeout():
    calls = []
    logs = []

    class Reader:
        def wait_for_code(self, timestamp, timeout):
            calls.append((timestamp, timeout))
            if len(calls) == 1:
                raise TimeoutError("mailbox timeout")
            return "123456"

    class Client:
        def __init__(self):
            self.begin_calls = []

        def begin(self, email):
            self.begin_calls.append(email)

    client = Client()
    assert rebind_module._wait_for_rebind_code(Reader(), client, "new@example.com", 123.0, logs.append) == "123456"
    assert calls == [(123.0, rebind_module.REBIND_OTP_FIRST_WAIT_SECONDS), (123.0, rebind_module.REBIND_OTP_RETRY_WAIT_SECONDS)]
    assert client.begin_calls == ["new@example.com"]
    assert any("自动重新请求一次" in message for message in logs)
