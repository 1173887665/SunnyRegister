from dataclasses import replace
from unittest.mock import MagicMock

from sunny_core import worker
from sunny_core.auth_resilience import classify_auth_failure


def _exercise_proxy_rotation(monkeypatch, error):
    db = MagicMock()
    payload = {
        "proxy_pool": [
            "http://proxy-one.example:8080",
            "http://proxy-two.example:8080",
        ],
        "proxy_ids": [1, 2],
    }
    selected = []
    clock = [0.0]

    def prepare(_db, current_payload, _email, slot):
        excluded = set(current_payload.get("_excluded_register_proxies") or [])
        address = "http://proxy-one.example:8080" if not excluded else "http://proxy-two.example:8080"
        return {"register": address, "mode": "proxy_pool", "slot": slot}

    def execute(_db, account, proxy, _log):
        selected.append(proxy)
        if len(selected) == 1:
            raise error
        return {"email": account["email"], "status": "success"}

    monkeypatch.setattr(worker, "_prepare_register_proxy", prepare)
    monkeypatch.setattr(worker, "rebind_one", execute)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    result = worker._rebind_with_proxy_rotation(
        db,
        payload,
        {"id": 1, "email": "rotate@example.com"},
        0,
    )
    return db, result, selected


def test_rebind_rotates_proxy_when_begin_is_rate_limited(monkeypatch):
    error = RuntimeError("/change_email/begin HTTP 429: Too many requests")
    error.rebind_phase = "begin"

    failure = classify_auth_failure(error)
    assert failure.category == "rate_limited"
    assert failure.rotate_proxy is True
    monkeypatch.setattr(worker, "classify_auth_failure", lambda _error: replace(failure, delay_seconds=0))

    db, result, selected = _exercise_proxy_rotation(monkeypatch, error)

    assert result["status"] == "success"
    assert selected == ["http://proxy-one.example:8080", "http://proxy-two.example:8080"]
    detail = db.event.call_args_list[-1].kwargs["detail"]
    assert detail["rebind_phase"] == "begin"
    assert detail["proxy_error_category"] == "rate_limited"


def test_rebind_rotates_proxy_when_delivery_was_not_observed(monkeypatch):
    error = RuntimeError("上游未实际投递换绑验证码：CloudMail 收件箱为空")
    error.rebind_phase = "delivery"

    db, result, selected = _exercise_proxy_rotation(monkeypatch, error)

    assert result["status"] == "success"
    assert selected == ["http://proxy-one.example:8080", "http://proxy-two.example:8080"]
    detail = db.event.call_args_list[-1].kwargs["detail"]
    assert detail["rebind_phase"] == "delivery"


def test_rebind_failure_detail_preserves_child_exception_traceback():
    error = RuntimeError("换绑子进程失败")
    error.remote_exception_type = "RebindError"
    error.remote_traceback = "Traceback (most recent call last):\n  File \"rebind.py\", line 1\nRebindError: failed"
    error.rebind_phase = "verify"

    detail = worker._rebind_failure_detail("old@example.com", error)

    assert detail["exception_type"] == "RebindError"
    assert detail["rebind_phase"] == "verify"
    assert 'File "rebind.py", line 1' in detail["traceback"]
