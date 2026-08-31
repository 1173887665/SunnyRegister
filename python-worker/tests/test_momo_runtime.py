from __future__ import annotations

import time

from momo_runtime.app.src.momo_core.momo_manager import MomoManager


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
