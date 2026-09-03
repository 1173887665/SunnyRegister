from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import link_workbench_worker as worker


def _request(**overrides):
    values = {
        "token": "fixture-token",
        "checkout_proxies": ["http://checkout-proxy"],
        "promotion_proxies": ["http://promotion-proxy"],
    }
    values.update(overrides)
    return worker.CheckoutRequest(**values)


def test_health_reports_workbench_runtime_and_state_root() -> None:
    adapter = types.ModuleType("tools.pay153_checkout.workbench_adapter")
    adapter.runtime_snapshot = lambda: {
        "jobs": 0,
        "pending": 0,
        "active": 0,
        "state_root": "D:/AI/SunnyRegister/data/link-workbench",
    }
    with patch.dict(sys.modules, {"tools.pay153_checkout.workbench_adapter": adapter}):
        payload = worker.health()
    assert payload["ok"] is True
    assert payload["service"] == "link-workbench-worker"
    assert payload["runtime"] == "workbench"
    assert "link-workbench" in payload["state_root"]


def test_health_probe_remains_public_when_token_configured() -> None:
    with patch.object(worker, "WORKER_TOKEN", "fixture-secret"):
        payload = worker.health()
    assert payload["ok"] is True


def test_start_checkout_passes_request_to_workbench_adapter() -> None:
    adapter = types.ModuleType("tools.pay153_checkout.workbench_adapter")
    captured = {}

    def start_checkout(payload):
        captured.update(payload)
        return "workbench-job-1"

    adapter.start_checkout = start_checkout
    with patch.dict(sys.modules, {"tools.pay153_checkout.workbench_adapter": adapter}):
        result = worker.start_checkout(_request(proxy_slot=4), authorization=None)
    assert result == {"ok": True, "job_id": "workbench-job-1"}
    assert captured["proxy_slot"] == 4


def test_unknown_job_returns_not_found() -> None:
    adapter = types.ModuleType("tools.pay153_checkout.workbench_adapter")
    adapter.checkout_status = lambda _job_id: None
    with patch.dict(sys.modules, {"tools.pay153_checkout.workbench_adapter": adapter}):
        with pytest.raises(HTTPException) as exc:
            worker.checkout_job("missing", authorization=None)
    assert exc.value.status_code == 404


def test_cancel_route_reports_adapter_result() -> None:
    adapter = types.ModuleType("tools.pay153_checkout.workbench_adapter")
    adapter.cancel_checkout = lambda job_id: job_id == "known"
    with patch.dict(sys.modules, {"tools.pay153_checkout.workbench_adapter": adapter}):
        assert worker.cancel_checkout_job("known", authorization=None) == {
            "ok": True,
            "job_id": "known",
        }
        assert worker.cancel_checkout_job("missing", authorization=None) == {
            "ok": False,
            "job_id": "missing",
        }
