from __future__ import annotations

import json
from unittest.mock import patch

from sunny_core.mailbox import MailAccount
from sunny_core.protocol_auth import ProtocolChallengeRequired, ProtocolRegistrationFlow


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", url=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = text.encode("utf-8") if text else json_bytes(payload)
        self.headers = {"content-type": "application/json" if payload is not None else "text/html"}
        self.url = url

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeCookies:
    def __init__(self):
        self.values = {
            "oai-did": "device-id",
            "__Secure-next-auth.session-token": "session-token",
            "_account": "account-id",
        }
        self.jar = []

    def get(self, name):
        return self.values.get(name)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.cookies = FakeCookies()
        self.headers = {"user-agent": "test-agent"}
        self.closed = False

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class FakeReader:
    instances = []

    def __init__(self, account, log, proxy_url):
        self.account = account
        self.proxy_url = proxy_url
        self.connected = False
        self.closed = False
        self.__class__.instances.append(self)

    def connect(self):
        self.connected = True

    def wait_for_code(self, _min_timestamp, timeout=10):
        assert timeout == 10
        return "123456"

    def close(self):
        self.closed = True


def sentinel_response():
    return FakeResponse(payload={"token": "sentinel-challenge", "proofofwork": {"required": False}})


def json_bytes(payload):
    return json.dumps(payload or {}, separators=(",", ":")).encode("utf-8")


def test_protocol_registration_completes_without_browser() -> None:
    responses = [
        FakeResponse(text="landing"),
        FakeResponse(payload={"csrfToken": "csrf-token"}),
        FakeResponse(payload={"url": "https://auth.openai.com/authorize"}),
        FakeResponse(text="auth page"),
        sentinel_response(),
        FakeResponse(payload={"page": {"type": "password"}, "continue_url": "https://auth.openai.com/create-account/password"}),
        FakeResponse(text="password page"),
        sentinel_response(),
        FakeResponse(payload={"page": {"type": "email_otp_verification"}, "continue_url": "https://auth.openai.com/email-verification"}),
        FakeResponse(text="verification page"),
        FakeResponse(payload={"continue_url": "https://auth.openai.com/email-verification"}),
        FakeResponse(payload={"page": {"type": "about_you"}, "continue_url": "https://auth.openai.com/about-you"}),
        FakeResponse(payload={"page": {"type": "about_you"}}),
        sentinel_response(),
        FakeResponse(payload={"continue_url": "https://chatgpt.com/api/auth/callback/openai?code=test"}),
        FakeResponse(text="callback"),
        FakeResponse(payload={"accessToken": "access-token", "account": {"id": "account-id", "planType": "plus"}}),
    ]
    session = FakeSession(responses)
    account = MailAccount(
        email="user@outlook.com",
        password="MailboxPass123!",
        client_id="client-id",
        refresh_token="mail-refresh-token",
        raw="user@outlook.com----MailboxPass123!----client-id----mail-refresh-token",
    )
    checkpoints = []
    with patch("sunny_core.protocol_auth.create_mailbox_reader", FakeReader):
        result = ProtocolRegistrationFlow(
            account,
            "http://proxy.example:8080",
            session=session,
            on_progress=lambda checkpoint, _snapshot: checkpoints.append(checkpoint),
        ).run()

    assert result["access_token"] == "access-token"
    assert result["plan_type"] == "plus"
    assert result["auth_action"] == "register"
    assert result["execution_mode"] == "protocol"
    assert result["protocol_challenge_strategy"] == "native_headless"
    assert result["sentinel_runtime_used"] is False
    assert result["protocol_traffic"]["requests"] == 17
    assert result["protocol_traffic"]["total_bytes"] > 0
    assert checkpoints == ["protocol_started", "email_submitted", "email_verified", "auth_completed", "registered"]
    assert session.closed is True
    assert not session.responses
    urls = [url for _method, url, _kwargs in session.requests]
    assert "https://chatgpt.com/api/auth/session" in urls
    assert all("playwright" not in url and "camoufox" not in url for url in urls)
    assert FakeReader.instances[-1].proxy_url == "http://proxy.example:8080"
    assert FakeReader.instances[-1].closed is True


def test_initial_email_verification_redirect_skips_duplicate_authorize_submit() -> None:
    account = MailAccount("user@outlook.com", "MailboxPass123!", "client-id", "mail-refresh-token", "raw")
    flow = ProtocolRegistrationFlow(account, session=FakeSession([]))
    flow.auth_page_url = "https://auth.openai.com/email-verification"
    flow._start_next_auth = lambda: None
    flow._verify_email = lambda _url, **_kwargs: {"page": {"type": "login_success"}, "continue_url": "https://chatgpt.com/callback"}
    flow._finish_session = lambda _url: {
        "access_token": "access",
        "session_json": {"accessToken": "access"},
        "auth_action": "login",
        "protocol_traffic": flow.traffic.snapshot(),
    }

    with (
        patch("sunny_core.protocol_auth.create_mailbox_reader", FakeReader),
        patch.object(flow, "_authorize_email") as duplicate_authorize,
    ):
        result = flow.run()

    assert result["access_token"] == "access"
    duplicate_authorize.assert_not_called()


def test_sentinel_device_challenge_stops_protocol_flow() -> None:
    flow = ProtocolRegistrationFlow(
        MailAccount("user@outlook.com", "password", "client-id", "refresh-token", "raw"),
        session=FakeSession(
            [
                FakeResponse(
                    payload={
                        "token": "sentinel-challenge",
                        "proofofwork": {"required": False},
                        "turnstile": {"required": False, "dx": "device-challenge"},
                    }
                )
            ]
        ),
    )
    flow.device_id = "device-id"

    try:
        flow._sentinel_headers("oauth_create_account")
    except ProtocolChallengeRequired as exc:
        assert "browser challenge" in str(exc)
        assert getattr(exc, "traffic")["requests"] == 1
    else:
        raise AssertionError("device challenge must stop protocol mode")


def test_sentinel_protocol_strategy_uses_narrow_runtime_headers() -> None:
    class FakeRuntime:
        def build_headers(self, **kwargs):
            assert kwargs["flow"] == "oauth_create_account"
            assert kwargs["device_id"] == "device-id"
            return {
                "openai-sentinel-token": "runtime-token",
                "openai-sentinel-so-token": "observer-token",
            }

    flow = ProtocolRegistrationFlow(
        MailAccount("user@outlook.com", "password", "client-id", "refresh-token", "raw"),
        session=FakeSession(
            [
                FakeResponse(
                    payload={
                        "token": "sentinel-challenge",
                        "proofofwork": {"required": False},
                        "turnstile": {"required": True, "dx": "device-challenge"},
                    }
                )
            ]
        ),
        challenge_strategy="sentinel_protocol",
    )
    flow.device_id = "device-id"
    flow._sentinel_runtime = FakeRuntime()

    headers = flow._sentinel_headers("oauth_create_account")

    assert headers["openai-sentinel-token"] == "runtime-token"
    assert headers["openai-sentinel-so-token"] == "observer-token"


def test_verify_email_can_reuse_page_loaded_by_auth_redirect() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={"page": {"type": "about_you"}, "continue_url": "https://auth.openai.com/about-you"}
            )
        ]
    )
    flow = ProtocolRegistrationFlow(
        MailAccount("user@outlook.com", "password", "client-id", "refresh-token", "raw"),
        session=session,
    )
    flow.reader = FakeReader(flow.account, lambda _message: None, "")
    flow._wait_for_email_code = lambda _timestamp: "123456"

    result = flow._verify_email(
        "https://auth.openai.com/email-verification",
        request_code=False,
        load_page=False,
    )

    assert result["page"]["type"] == "about_you"
    assert len(session.requests) == 1
    assert session.requests[0][1].endswith("/api/accounts/email-otp/validate")
