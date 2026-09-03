import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from sunny_core import mailbox as mailbox_module
from sunny_core import domain_mail_cleanup as cleanup_module
from sunny_core import rebind as rebind_module
from sunny_core.db import SunnyDB
from sunny_core.mailbox import DomainMailReader, URLAPIICloudReader, account_from_row, create_mailbox_reader
from sunny_core.protocol_auth import ProtocolChallengeRequired


def _credential():
    return json.dumps({"base_url": "https://mail.example", "auth_token": "token-1"})


def test_rebound_mailbox_credentials_are_used_for_bulk_mailbox_selection():
    row = {
        "email": "original@icloud.com",
        "access_key": "https://legacy.example/messages/original",
        "mailbox_type": "apple",
        "mailbox_channel": "url_api",
        "rebind_email": "replacement@example.com",
        "rebind_mailbox_api": "https://sunny.example/api/sunny/domain-mail/pickup?email=replacement%40example.com&token=dmsk_one",
    }

    effective = SunnyDB._apply_rebind_mailbox_credentials(row)

    assert effective["email"] == "replacement@example.com"
    assert effective["access_key"] == effective["rebind_mailbox_api"]
    assert effective["raw"] == f"replacement@example.com----{effective['access_key']}"
    assert effective["mailbox_type"] == "domain"
    assert effective["mailbox_channel"] == "domain_api"
    account = account_from_row(effective)
    assert account.email == "replacement@example.com"
    assert account.mailbox_type == "domain"


def test_rebound_url_api_mailbox_preserves_apple_channel():
    row = {
        "email": "original@icloud.com",
        "access_key": "https://legacy.example/messages/original",
        "mailbox_type": "apple",
        "mailbox_channel": "url_api",
        "rebind_email": "replacement@example.com",
        "rebind_mailbox_api": "https://mail.example/inbox/replacement",
    }

    effective = SunnyDB._apply_rebind_mailbox_credentials(row)

    assert effective["email"] == "replacement@example.com"
    assert effective["mailbox_type"] == "apple"
    assert effective["mailbox_channel"] == "url_api"


def test_rebind_target_recognizes_local_pickup_url_despite_historical_apple_type():
    pickup_url = "http://127.0.0.1/api/sunny/domain-mail/pickup?email=replacement%40example.com&token=dmsk_one"
    mailbox_type, mailbox_channel = rebind_module._resolve_rebind_mailbox_kind(
        pickup_url, "replacement@example.com", "apple", "url_api"
    )

    assert (mailbox_type, mailbox_channel) == ("domain", "domain_api")
    account = rebind_module._rebind_target_account(
        "replacement@example.com", pickup_url, mailbox_type, mailbox_channel,
        account_from_row({"email": "source@example.com", "raw": "source@example.com----password----11111111-1111-1111-1111-111111111111----refresh"}),
    )
    assert isinstance(create_mailbox_reader(account, None), DomainMailReader)


def test_rebind_target_keeps_explicit_apple_url_api_for_external_url():
    api = "https://mail.example/inbox/replacement"
    mailbox_type, mailbox_channel = rebind_module._resolve_rebind_mailbox_kind(
        api, "replacement@example.com", "apple", "url_api"
    )

    assert (mailbox_type, mailbox_channel) == ("apple", "url_api")
    account = rebind_module._rebind_target_account(
        "replacement@example.com", api, mailbox_type, mailbox_channel,
        account_from_row({"email": "source@example.com", "raw": "source@example.com----password----11111111-1111-1111-1111-111111111111----refresh"}),
    )
    assert isinstance(create_mailbox_reader(account, None), URLAPIICloudReader)


def test_historical_domain_row_with_impersonate_url_routes_to_url_api():
    access_key = "https://a-mail.sanai.pro/?impersonate_email=replacement@example.com&impersonate_uuid=uuid-1"
    account = account_from_row({
        "email": "replacement@example.com",
        "mailbox_type": "domain",
        "mailbox_channel": "domain_api",
        "access_key": access_key,
    })
    assert account.mailbox_type == "apple"
    assert account.mailbox_channel == "url_api"
    assert account.access_key == access_key


def test_historical_rebind_row_infers_external_url_channel():
    row = {
        "email": "original@icloud.com",
        "mailbox_type": "domain",
        "mailbox_channel": "domain_api",
        "rebind_email": "replacement@example.com",
        "rebind_mailbox_api": "https://a-mail.sanai.pro/?impersonate_email=replacement@example.com&impersonate_uuid=uuid-1",
    }
    effective = SunnyDB._apply_rebind_mailbox_credentials(row)
    assert effective["mailbox_type"] == "apple"
    assert effective["mailbox_channel"] == "url_api"


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


def test_domain_reader_parses_json_string_data_and_combines_body_fields(monkeypatch):
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": _credential()}),
        None,
    )
    monkeypatch.setattr(
        reader,
        "_request",
        lambda: {"data": json.dumps({"items": [{"id": "m1", "text": "Your code", "content": "<b>654321</b>"}]})},
    )
    current = reader._latest()
    assert current["id"] == "m1"
    assert current["code"] == "654321"


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


def test_domain_reader_retries_without_optional_filters(monkeypatch):
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": _credential()}),
        None,
    )
    calls = []

    class Response:
        status_code = 200
        ok = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        @staticmethod
        def close():
            return None

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        if "type" in kwargs["json"]:
            return Response({"data": []})
        return Response({"data": [{"id": "m1", "bodyPreview": "code 654321"}]})

    monkeypatch.setattr(mailbox_module.requests, "post", fake_post)
    assert reader._latest()["code"] == "654321"
    assert len(calls) == 2
    assert "type" not in calls[1] and "isDel" not in calls[1]


def test_domain_reader_keeps_otp_when_provider_recipient_field_differs(monkeypatch):
    logs = []
    reader = DomainMailReader(
        account_from_row({"email": "user@example.com", "mailbox_type": "domain", "access_key": _credential()}),
        logs.append,
    )
    monkeypatch.setattr(reader, "_request", lambda: {"data": [{
        "id": "m1",
        "to": "legacy-recipient-field",
        "receivedAt": "2099-01-01T00:00:00Z",
        "bodyPreview": "ChatGPT code 654321",
    }]})

    assert reader._latest()["code"] == "654321"
    assert reader.last_raw_count == 1
    assert reader.last_recipient_match_count == 0
    assert reader.last_recipient_mismatch_count == 1
    assert any("收件人不匹配 1" in message for message in logs)


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
    assert any("取件地址=" in message for message in logs)
    assert all(pickup_token not in message for message in logs)
    assert any("token=%3Credacted%3E" in message for message in logs)


def test_rebind_imported_pickup_credential_hashes_token():
    token = "dmsk_imported-token"
    credential = f"https://sunny.example/api/sunny/domain-mail/pickup?email=new%40example.com&token={token}"

    assert rebind_module._pickup_token_hash(credential) == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert rebind_module._pickup_token_hash("not-a-url") == ""


def test_rebind_resends_twice_after_otp_delivery_timeouts():
    calls = []
    logs = []

    class Reader:
        def wait_for_code(self, timestamp, timeout):
            calls.append((timestamp, timeout))
            if len(calls) < 3:
                raise TimeoutError("mailbox timeout")
            return "123456"

    class Client:
        def __init__(self):
            self.begin_calls = []

        def begin(self, email):
            self.begin_calls.append(email)

    client = Client()
    assert rebind_module._wait_for_rebind_code(Reader(), client, "new@example.com", 123.0, logs.append) == "123456"
    assert calls == [
        (123.0, 20),
        (123.0, 45),
        (123.0, 45),
    ]
    assert client.begin_calls == ["new@example.com", "new@example.com"]
    assert any("进行第 1 次重发" in message for message in logs)
    assert any("进行第 2 次重发" in message for message in logs)


def test_rebind_empty_mailbox_distinguishes_accepted_but_undelivered_code():
    class Reader:
        last_raw_count = 0
        last_status = 200
        last_error = ""

        def wait_for_code(self, _timestamp, timeout):
            assert timeout in {20, 45}
            raise TimeoutError("mailbox empty")

    class Client:
        last_begin_accepted = True

        def begin(self, _email):
            return {"success": True}

    with pytest.raises(TimeoutError, match="上游未实际投递换绑验证码"):
        rebind_module._wait_for_rebind_code(Reader(), Client(), "new@example.com", 123.0, lambda _message: None)


def test_rebind_begin_retries_transient_network_error():
    calls = []
    logs = []

    class Client:
        def begin(self, email):
            calls.append(email)
            if len(calls) == 1:
                raise rebind_module.RebindError("换绑接口网络请求失败：curl timeout")
            return {"success": True}

    assert rebind_module._begin_with_retry(Client(), "new@example.com", logs.append) == {"success": True}
    assert calls == ["new@example.com", "new@example.com"]
    assert any("瞬时网络错误" in message for message in logs)


def test_rebind_begin_rejects_http_success_with_failed_payload():
    class Client:
        def begin(self, email):
            return {"success": False, "message": "request rejected"}

    try:
        rebind_module._begin_with_retry(Client(), "new@example.com", lambda _: None)
    except rebind_module.RebindError as exc:
        assert "request rejected" in str(exc)
    else:
        raise AssertionError("success=false response must fail before OTP polling")


def test_rebound_account_login_retries_transient_certificate_error(monkeypatch):
    account = account_from_row({
        "email": "replacement@example.com",
        "mailbox_type": "apple",
        "mailbox_channel": "url_api",
        "access_key": "https://mail.example/inbox",
    })
    expected = (object(), {"access_token": "access"})
    calls = []

    def login(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise RuntimeError("curl: (60) unable to get local issuer certificate")
        return expected

    logs = []
    monkeypatch.setattr(rebind_module, "_login_flow", login)

    assert rebind_module._login_rebound_account(
        account, "http://proxy.example:8080", logs.append, should_cancel=None
    ) == expected
    assert len(calls) == 2
    assert any("建立全新认证会话重试一次" in message for message in logs)


def test_failed_domain_mailbox_retention_defaults_to_enabled():
    assert cleanup_module.retain_failed_mailbox({}) is True
    assert cleanup_module.retain_failed_mailbox({"retain_failed_mailboxes": False}) is False
    assert cleanup_module.retain_failed_mailbox({"retain_failed_mailboxes": "off"}) is False


def test_cloudmail_failed_mailbox_delete_accepts_public_delete_extension(monkeypatch):
    calls = []

    class Response:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"code": 200, "message": "success"}

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()

    monkeypatch.setattr(cleanup_module.requests, "request", request)
    cleanup_module.delete_cloudmail_user({"base_url": "https://cloudmail.example", "auth_token": "token", "site_password": "password"}, "failed@example.com")
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/public/deleteUser")
    assert calls[0][2]["params"] == {"email": "failed@example.com"}
    assert calls[0][2]["headers"]["x-custom-auth"] == "password"


def test_failed_mailbox_cleanup_removes_local_row_when_cloudmail_delete_fails(monkeypatch):
    class DB:
        deleted = False

        def delete_failed_domain_mailbox(self, email, pickup_token_hash):
            self.deleted = True
            return True

    monkeypatch.setattr(cleanup_module, "delete_cloudmail_user", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("HTTP 404")))
    db = DB()
    try:
        cleanup_module.cleanup_failed_mailbox(db, {"retain_failed_mailboxes": False}, "failed@example.com", "hash", lambda _: None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("CloudMail deletion failure must be surfaced")
    assert db.deleted is True


def test_rebind_failure_retention_policy_controls_persistence(monkeypatch):
    class DB:
        def __init__(self, retain):
            self.retain = retain
            self.persisted = False

        def get_config(self, key):
            assert key == "domain_mailbox"
            return {"retain_failed_mailboxes": self.retain}

        def persist_rebind_failure(self, *args):
            self.persisted = True

    cleanup_calls = []
    monkeypatch.setattr(rebind_module, "cleanup_failed_mailbox", lambda *args: cleanup_calls.append(args) or True)

    retained = DB(True)
    rebind_module._handle_failed_domain_mailbox(retained, "old@example.com", "new@example.com", "pickup", "hash", RuntimeError("failed"), lambda _: None)
    assert retained.persisted is True
    assert cleanup_calls == []

    discarded = DB(False)
    rebind_module._handle_failed_domain_mailbox(discarded, "old@example.com", "new@example.com", "pickup", "hash", RuntimeError("failed"), lambda _: None)
    assert discarded.persisted is False
    assert len(cleanup_calls) == 1


def test_external_url_rebind_failure_preserves_url_api_type():
    db = SunnyDB.__new__(SunnyDB)
    db.conn = sqlite3.connect(":memory:")
    db.conn.row_factory = sqlite3.Row
    db.conn.execute(
        "create table sunny_mailbox_groups(id integer primary key autoincrement,name text unique,description text,created_at text,updated_at text)"
    )
    db.conn.execute(
        """create table sunny_mailboxes(
        id integer primary key autoincrement,group_id integer,email text,mailbox_type text,mailbox_channel text,
        access_key text,pickup_token_hash text,raw text,status text,enabled integer,last_error text,
        latest_mail_json text,created_at text,updated_at text)"""
    )
    access_url = "https://a-mail.sanai.pro/?impersonate_email=new@example.com&impersonate_uuid=uuid-1"

    db.persist_rebind_failure("old@example.com", "new@example.com", access_url, "", "failed")

    row = db.conn.execute("select * from sunny_mailboxes where email='new@example.com'").fetchone()
    assert row["mailbox_type"] == "apple"
    assert row["mailbox_channel"] == "url_api"
    assert row["pickup_token_hash"] == ""


def test_external_url_rebind_cleanup_does_not_call_cloudmail(monkeypatch):
    class DB:
        deleted = False

        @staticmethod
        def get_config(key):
            assert key == "domain_mailbox"
            return {"retain_failed_mailboxes": False}

        def delete_failed_domain_mailbox(self, email, pickup_token_hash):
            self.deleted = (email, pickup_token_hash)
            return True

    monkeypatch.setattr(rebind_module, "cleanup_failed_mailbox", lambda *args: (_ for _ in ()).throw(AssertionError("unexpected CloudMail cleanup")))
    db = DB()
    access_url = "https://a-mail.sanai.pro/?impersonate_email=new@example.com&impersonate_uuid=uuid-1"

    rebind_module._handle_failed_domain_mailbox(
        db, "old@example.com", "new@example.com", access_url, "", RuntimeError("failed"), lambda _: None
    )

    assert db.deleted == ("new@example.com", "")


def test_rebind_client_observation_header_matches_web_format():
    class Session:
        cookies = type("Cookies", (), {"jar": []})()

    flow = type("Flow", (), {"session": Session(), "device_id": "device-id", "_last_access_token": "access-token"})()
    client = rebind_module.ChangeEmailClient(flow, "account-id")
    header = client._headers(rebind_module.BEGIN_PATH, json_body=True)["x-oai-is-client-observation"]
    assert re.fullmatch(r"v1\.r\.p\.[A-Za-z0-9_-]{16}", header)


def test_rebind_client_reports_account_cooldown_clearly():
    class Response:
        status_code = 400
        text = '{"detail":{"code":"email_change_account_too_new"}}'
        headers = {}

        @staticmethod
        def json():
            return {"detail": {"code": "email_change_account_too_new"}}

    class Session:
        cookies = type("Cookies", (), {"jar": []})()

        @staticmethod
        def request(*_args, **_kwargs):
            return Response()

    flow = type("Flow", (), {"session": Session(), "device_id": "device-id", "_last_access_token": "access-token"})()
    client = rebind_module.ChangeEmailClient(flow, "account-id")

    with pytest.raises(rebind_module.RebindError, match="email_change_account_too_new") as exc_info:
        client.begin("replacement@example.com")

    assert "换绑冷却期" in str(exc_info.value)


def test_rebind_login_falls_back_to_headless_browser_after_sentinel_challenge(monkeypatch):
    account = account_from_row({
        "email": "original@example.com",
        "raw": "original@example.com----mail-password----client-id----refresh-token",
        "chatgpt_password": "chatgpt-password",
        "totp_secret": "JBSWY3DPEHPK3PXP",
    })
    challenge = ProtocolChallengeRequired("Sentinel 协议运行时初始化失败: TypedArray Xray")
    challenge.traffic = {"requests": 4, "total_bytes": 1024}
    browser_result = {
        "access_token": "browser-access-token",
        "session_json": {"accessToken": "browser-access-token", "account": {"id": "browser-account-id"}},
        "storage_state_json": {
            "cookies": [
                {"name": "oai-did", "value": "browser-device-id", "domain": ".chatgpt.com", "path": "/", "secure": True},
                {"name": "__Secure-next-auth.session-token", "value": "browser-session", "domain": "chatgpt.com", "path": "/", "secure": True},
            ],
            "origins": [],
        },
    }
    logs = []
    monkeypatch.setattr(rebind_module.ProtocolRegistrationFlow, "run", lambda _flow: (_ for _ in ()).throw(challenge))
    calls = []

    def browser_login(*args, **kwargs):
        calls.append((args, kwargs))
        return browser_result

    monkeypatch.setattr(rebind_module, "login_or_register", browser_login)

    flow, result = rebind_module._login_flow(account, "http://proxy.example:8080", logs.append, keep_session=True)

    assert result["access_token"] == "browser-access-token"
    assert result["execution_mode"] == "protocol_headless_fallback"
    assert result["protocol_traffic"] == challenge.traffic
    assert flow.device_id == "browser-device-id"
    assert flow._last_access_token == "browser-access-token"
    assert result["account_id"] == "browser-account-id"
    assert "__Secure-next-auth.session-token=browser-session" in rebind_module._cookie_header(flow.session)
    assert calls[0][1]["existing_account"] is True
    assert calls[0][1]["require_refresh_token"] is False
    assert calls[0][1]["execution_mode"] == "protocol_headless_fallback"
    assert any("自动切换 Camoufox" in message for message in logs)
    assert any("继续执行换绑接口" in message for message in logs)


def test_rebind_login_falls_back_to_browser_mailbox_after_browser_ls_failure(monkeypatch):
    account = account_from_row({
        "email": "original@example.com",
        "raw": "original@example.com----mail-password----client-id----refresh-token",
        "chatgpt_password": "chatgpt-password",
        "totp_secret": "JBSWY3DPEHPK3PXP",
    })
    challenge = ProtocolChallengeRequired("Sentinel challenge")
    created = []

    class Session:
        def __init__(self):
            self.closed = False
            self.cookies = type("Cookies", (), {"jar": [], "clear": lambda self: None, "set": lambda self, *args, **kwargs: None})()

        def close(self):
            self.closed = True

    class FakeFlow:
        def __init__(self, flow_account, proxy_url, log, **kwargs):
            self.account = flow_account
            self.proxy_url = proxy_url
            self.session = Session()
            self.device_id = "protocol-device"
            self._last_access_token = ""
            self.kwargs = kwargs
            created.append(self)

        def _new_session(self):
            return Session()

        def run(self):
            raise challenge

    browser_result = {
        "access_token": "mailbox-access-token",
        "account_id": "mailbox-account-id",
        "session_json": {"account": {"id": "mailbox-account-id"}},
        "storage_state_json": {
            "cookies": [
                {"name": "oai-did", "value": "mailbox-device-id", "domain": ".chatgpt.com", "path": "/", "secure": True},
                {"name": "session", "value": "mailbox-session", "domain": "chatgpt.com", "path": "/", "secure": True},
            ],
            "origins": [],
        },
    }
    calls = []

    def browser_login(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise RuntimeError("OpenAI 未提供邮箱验证码切换入口，且当前 LS 登录未完成")
        return browser_result

    logs = []
    monkeypatch.setattr(rebind_module, "ProtocolRegistrationFlow", FakeFlow)
    monkeypatch.setattr(rebind_module, "login_or_register", browser_login)

    flow, result = rebind_module._login_flow(account, "http://proxy.example:8080", logs.append, keep_session=True)

    assert len(created) == 2
    assert calls[1][0][0].has_login_secret is False
    assert calls[1][1]["existing_account"] is True
    assert calls[1][1]["require_refresh_token"] is False
    assert calls[1][1]["execution_mode"] == "protocol_headless_fallback"
    assert flow is created[1]
    assert result["access_token"] == "mailbox-access-token"
    assert result["execution_mode"] == "protocol_headless_fallback"
    assert result["protocol_fallback"] == "mailbox_browser"
    assert any("浏览器邮箱验证码登录" in message for message in logs)


def test_rebind_login_rebuilds_stale_auth_with_mailbox_protocol(monkeypatch):
    account = account_from_row({
        "email": "original@example.com",
        "raw": "original@example.com----mail-password----client-id----refresh-token",
        "chatgpt_password": "chatgpt-password",
        "totp_secret": "JBSWY3DPEHPK3PXP",
    })
    created = []

    class Session:
        def __init__(self):
            self.cookies = type("Cookies", (), {"jar": []})()

        def close(self):
            return None

    class FakeFlow:
        def __init__(self, flow_account, proxy_url, log, **kwargs):
            self.account = flow_account
            self.proxy_url = proxy_url
            self.log = log
            self.kwargs = kwargs
            self.session = Session()
            self.reader = None
            self.device_id = "device-id"
            self._last_access_token = ""
            created.append(self)

        def run(self):
            if self.account.has_login_secret:
                raise RuntimeError("Send email verification code failed: invalid_auth_step")
            return {"access_token": "mailbox-protocol-token", "account_id": "account-id"}

    browser_calls = []
    monkeypatch.setattr(rebind_module, "ProtocolRegistrationFlow", FakeFlow)
    monkeypatch.setattr(rebind_module, "login_or_register", lambda *args, **kwargs: browser_calls.append((args, kwargs)))

    flow, result = rebind_module._login_flow(account, "", lambda _message: None, keep_session=True)

    assert len(created) == 2
    assert flow is created[1]
    assert flow.account.has_login_secret is False
    assert result["access_token"] == "mailbox-protocol-token"
    assert result["protocol_fallback"] == "mailbox_protocol"
    assert browser_calls == []


def test_rebind_login_does_not_mailbox_retry_when_account_is_deactivated():
    error = rebind_module.LoginSecretAuthenticationError("account_deactivated: account is disabled")
    assert rebind_module._should_use_mailbox_browser_fallback(error) is False
