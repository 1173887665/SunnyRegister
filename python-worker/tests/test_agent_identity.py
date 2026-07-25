from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

from sunny_core.agent_identity import AgentIdentityUnavailableError, AUTH_MODE, create_agent_identity_auth


def _jwt(claims: dict) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/v1/agent/register"):
            return FakeResponse({"agent_runtime_id": "runtime-id"})
        return FakeResponse({"encrypted_task_id": "task-id"})

    def close(self) -> None:
        self.closed = True


class FakeHTMLSession(FakeSession):
    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        response = FakeResponse({}, status_code=200)
        response.text = "<!doctype html><html><head><title>Just a moment...</title></head></html>"
        response.headers = {"Content-Type": "text/html; charset=UTF-8"}
        response.url = url
        response.json = lambda: (_ for _ in ()).throw(ValueError("unexpected character"))
        return response


class FakeCapabilityErrorSession(FakeSession):
    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        payload = base64.urlsafe_b64encode(json.dumps({
            "kind": "AuthApiFailure",
            "errorCode": "agent_registry_not_enabled",
            "requestId": "req-test",
        }).encode("utf-8")).decode("ascii").rstrip("=")
        response = FakeResponse({}, status_code=200)
        response.text = "<!doctype html><html><head><title>OpenAI</title></head></html>"
        response.headers = {"Content-Type": "text/html; charset=UTF-8"}
        response.url = f"https://auth.openai.com/error?payload={payload}"
        response.json = lambda: (_ for _ in ()).throw(ValueError("unexpected character"))
        return response


class AgentIdentityTests(unittest.TestCase):
    def test_create_agent_identity_uses_dynamic_signing_contract_without_token_leak(self) -> None:
        access_token = _jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-id",
                    "chatgpt_user_id": "user-id",
                    "chatgpt_plan_type": "plus",
                },
                "https://api.openai.com/profile": {"email": "user@example.com"},
            }
        )
        client = FakeSession()
        logs: list[str] = []
        with patch("sunny_core.agent_identity._session", return_value=client):
            result = create_agent_identity_auth(access_token, log=logs.append)

        self.assertEqual(result["auth_mode"], AUTH_MODE)
        self.assertEqual(AUTH_MODE, "agentIdentity")
        identity = result["agent_identity"]
        self.assertEqual(identity["agent_runtime_id"], "runtime-id")
        self.assertEqual(identity["task_id"], "task-id")
        self.assertEqual(identity["account_id"], "account-id")
        self.assertEqual(identity["chatgpt_user_id"], "user-id")
        self.assertEqual(identity["email"], "user@example.com")
        self.assertEqual(identity["plan_type"], "plus")
        self.assertGreater(len(base64.b64decode(identity["agent_private_key"])), 32)
        self.assertTrue(client.closed)
        self.assertTrue(all(access_token not in message for message in logs))
        self.assertEqual(
            client.calls[0][1]["headers"]["Authorization"],
            f"Bearer {access_token}",
        )
        self.assertTrue(client.calls[1][1]["json"]["signature"])

    def test_create_agent_identity_rejects_access_token_without_required_claims(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "account_id"):
            create_agent_identity_auth(_jwt({"https://api.openai.com/auth": {}}))

    def test_create_agent_identity_rejects_expired_access_token(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "已过期"):
            create_agent_identity_auth(
                _jwt(
                    {
                        "exp": 1,
                        "https://api.openai.com/auth": {
                            "chatgpt_account_id": "account-id",
                            "chatgpt_user_id": "user-id",
                        },
                    }
                )
            )

    def test_create_agent_identity_reports_html_instead_of_raw_json_error(self) -> None:
        access_token = _jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-id",
                    "chatgpt_user_id": "user-id",
                }
            }
        )
        with patch("sunny_core.agent_identity._session", return_value=FakeHTMLSession()):
            with self.assertRaisesRegex(
                RuntimeError,
                "Agent Identity 注册返回 HTML.*Just a moment",
            ):
                create_agent_identity_auth(access_token)

    def test_create_agent_identity_reports_account_capability_error(self) -> None:
        access_token = _jwt(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-id",
                    "chatgpt_user_id": "user-id",
                }
            }
        )
        with patch("sunny_core.agent_identity._session", return_value=FakeCapabilityErrorSession()):
            with self.assertRaisesRegex(
                AgentIdentityUnavailableError,
                "agent_registry_not_enabled.*账户侧能力限制.*Cloudflare 无关",
            ):
                create_agent_identity_auth(access_token)
