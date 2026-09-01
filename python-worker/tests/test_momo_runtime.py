from __future__ import annotations

import json
import time

from momo_runtime.app.src.momo_core.momo_manager import MomoManager
from momo_runtime.app.src.momo_core.momo_qr import parse_qr_payload


def _wait(manager: MomoManager, job_id: str, terminal: set[str]) -> dict:
    deadline = time.time() + 3
    while time.time() < deadline:
        job = manager.get_job(job_id) or {}
        if str(job.get("status")) in terminal:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not reach {terminal}: {manager.get_job(job_id)}")


def test_momo_register_and_qr_payment(tmp_path):
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    manager.import_phones("+84901234567----https://example.test/sms/1")

    register = manager.start_register(source="pool", pin="1234", skip_kyc=True)
    assert register["status"] == "running"
    _wait(manager, register["id"], {"waiting_otp"})
    assert manager.submit_otp(register["id"], "123456")
    finished = _wait(manager, register["id"], {"success", "failed"})
    assert finished["status"] == "success"
    assert manager.list_accounts()[0]["kyc_status"] == "skipped"

    payment = manager.start_payment(phone="+84901234567", qr_payload="momo://fixture/order/1", amount="50000")
    paid = _wait(manager, payment["id"], {"success", "failed"})
    assert paid["status"] == "success"
    assert paid["payment_id"].startswith("momo-fixture-")


def test_momo_qr_parser_accepts_json_and_rejects_invalid_amount() -> None:
    parsed = parse_qr_payload('{"url":"momo://merchant/1","amount":"125000","merchant":"Fixture Shop"}')
    assert parsed == {"payload": "momo://merchant/1", "amount": "125000", "merchant": "Fixture Shop"}
    try:
        parse_qr_payload("momo://merchant/1?amount=0")
    except ValueError as exc:
        assert "金额" in str(exc)
    else:
        raise AssertionError("zero QR amount must be rejected")


def test_momo_registration_otp_recovers_after_manager_restart(tmp_path) -> None:
    state_file = tmp_path / "state.json"
    pool_file = tmp_path / "pool.json"
    phone = "+84901234567"
    now = time.time()
    job_id = "recovered-register"
    state_file.write_text(json.dumps({
        "jobs": {
            job_id: {
                "id": job_id,
                "kind": "register",
                "phone": phone,
                "pin": "1234",
                "proxy": "",
                "source": "pool",
                "login_existing": False,
                "skip_kyc": True,
                "country": "VN",
                "phone_country_code": "84",
                "sms_provider": "pool",
                "sms_activation_id": "",
                "_sms_api_key": "",
                "_sms_config": {"phone_source": "pool"},
                "phone_pool_reserved": True,
                "otp_timeout_sec": 10,
                "profile": {},
                "_session": {"phone": phone, "proxy": "", "otp_sent": True},
                "status": "waiting_otp",
                "stage": "otp",
                "message": "等待 OTP",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "logs": [],
                "_otp": [],
                "_otp_deadline": now + 10,
            }
        },
        "accounts": {},
        "settings": {"mock_mode": True, "phone_source": "pool", "otp_timeout_sec": 10},
    }), encoding="utf-8")
    pool_file.write_text(json.dumps([{"phone": phone, "sms_url": "https://example.test/sms/1", "status": "reserved"}]), encoding="utf-8")

    manager = MomoManager(state_file=str(state_file), pool_file=str(pool_file))
    assert manager.submit_otp(job_id, "123456")
    recovered = _wait(manager, job_id, {"success", "failed"})
    assert recovered["status"] == "success"
    assert manager.list_accounts()[0]["phone"] == phone
    assert manager.list_phones()[0]["status"] == "used"
