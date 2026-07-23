from __future__ import annotations

from unittest.mock import patch

from sunny_core.mailbox import MailAccount
from sunny_core.protocol_auth import ProtocolRegistrationFlow


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

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
        FakeResponse(payload={"ok": True}),
        sentinel_response(),
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
    with patch("sunny_core.protocol_auth.HotmailReader", FakeReader):
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
    assert checkpoints == ["protocol_started", "email_submitted", "email_verified", "auth_completed", "registered"]
    assert session.closed is True
    assert not session.responses
    urls = [url for _method, url, _kwargs in session.requests]
    assert "https://chatgpt.com/api/auth/session" in urls
    assert all("playwright" not in url and "camoufox" not in url for url in urls)
    assert FakeReader.instances[-1].proxy_url == "http://proxy.example:8080"
    assert FakeReader.instances[-1].closed is True
