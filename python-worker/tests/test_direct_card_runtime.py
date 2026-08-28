from __future__ import annotations

import base64
import json
from unittest.mock import patch


def _token(account_id: str = "account_fixture") -> str:
    payload = {
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
        "https://api.openai.com/profile": {"email": f"{account_id}@example.test"},
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_direct_card_runtime_reports_isolated_protocol_info():
    from direct_card_runtime import manager

    info = manager.info()

    assert info["service"] == "direct-card-protocol"
    assert info["fingerprint_sticky_per_account"] is True
    assert info["aligned_batch_limit"] == 50


def test_direct_card_batch_preserves_aligned_start_contract():
    from direct_card_runtime import manager

    payload = {
        "tasks": [
            {
                "client_id": f"client-{index}",
                "payload": {
                    "access_token": _token(f"account-{index}"),
                    "flow_mode": "link_only",
                    "promo_proxy_pool": ["http://127.0.0.1:8080"],
                },
            }
            for index in range(3)
        ],
        "start_delay_ms": 250,
    }

    with patch.object(manager.threading, "Thread") as thread:
        result = manager.start_batch(payload)

    assert result["ok"] is True
    assert len(result["items"]) == 3
    assert len({item["task_id"] for item in result["items"]}) == 3
    assert thread.call_count == 3
    assert all(call.kwargs["daemon"] is True for call in thread.call_args_list)

    with manager.protocol_server.TASKS_LOCK:
        for item in result["items"]:
            task = manager.protocol_server.TASKS.pop(item["task_id"])
            assert task["status"] == "queued"
            assert task["start_group"] == result["start_group"]
