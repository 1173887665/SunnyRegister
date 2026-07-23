from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest

from sunny_core.agent_identity import AUTH_MODE, create_agent_identity_auth


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


def test_create_agent_identity_uses_dynamic_signing_contract_without_token_leak() -> None:
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

    assert result["auth_mode"] == AUTH_MODE == "agentIdentity"
    identity = result["agent_identity"]
    assert identity["agent_runtime_id"] == "runtime-id"
    assert identity["task_id"] == "task-id"
    assert identity["account_id"] == "account-id"
    assert identity["chatgpt_user_id"] == "user-id"
    assert identity["email"] == "user@example.com"
    assert identity["plan_type"] == "plus"
    assert len(base64.b64decode(identity["agent_private_key"])) > 32
    assert client.closed
    assert all(access_token not in message for message in logs)
    assert client.calls[0][1]["headers"]["Authorization"] == f"Bearer {access_token}"
    assert client.calls[1][1]["json"]["signature"]


def test_create_agent_identity_rejects_access_token_without_required_claims() -> None:
    with pytest.raises(RuntimeError, match="account_id"):
        create_agent_identity_auth(_jwt({"https://api.openai.com/auth": {}}))
