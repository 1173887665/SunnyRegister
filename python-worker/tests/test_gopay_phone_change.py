from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gopay_runtime" / "app" / "src"))


class FakeAuth:
    access_token = "new-access"
    refresh_token = "new-refresh"


class FakeClient:
    def __init__(self):
        self.auth = FakeAuth()
        self.user_uuid = "customer-1"
        self.requested_phone = ""
        self.verified = ""

    def refresh_token(self):
        return {"status": 200, "body": {}}

    def phone_change_request(self, phone, country_code="+62"):
        self.requested_phone = phone
        return {"status": 202, "body": {"data": {"otp_token": "otp-token", "verification_id": "verification-1"}}}

    def phone_change_verify(self, otp, *, otp_token="", verification_id=""):
        self.verified = otp
        assert otp_token == "otp-token"
        assert verification_id == "verification-1"
        return {"status": 200, "body": {"data": {}}}


def _wait_for(manager, job_id, status):
    deadline = time.time() + 5
    while time.time() < deadline:
        job = manager.get(job_id)
        if job and job.get("status") == status:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not reach {status}: {manager.get(job_id)}")


def test_phone_change_pool_updates_account_and_keeps_pool_row(monkeypatch, tmp_path):
    accounts_path = tmp_path / "accounts.json"
    pool_path = tmp_path / "pool.json"
    monkeypatch.setenv("OPAI_GOPAY_ACCOUNTS_FILE", str(accounts_path))
    accounts_path.write_text(json.dumps([{
        "phone": "+6281234567890",
        "local": "81234567890",
        "pin": "123456",
        "customer_id": "customer-1",
        "access_token": "old-access",
        "refresh_token": "old-refresh",
    }]), encoding="utf-8")
    pool_path.write_text(json.dumps([{"phone": "+6281234567891", "sms_url": "", "status": "available"}]), encoding="utf-8")

    from opai.core.payment_inbox import _PhoneChangeManager

    fake = FakeClient()
    manager = _PhoneChangeManager(pool_path=pool_path, client_factory=lambda account, phone: fake)
    created = manager.start(phone="+6281234567890", source="pool", replacement_phone="+6281234567891")
    waiting = _wait_for(manager, created["id"], "waiting_otp")
    assert waiting["new_phone"] == "+6281234567891"
    manager.submit_otp(created["id"], "654321")
    success = _wait_for(manager, created["id"], "success")

    saved = json.loads(accounts_path.read_text(encoding="utf-8"))
    assert saved[0]["phone"] == "+6281234567891"
    assert saved[0]["local"] == "81234567891"
    assert saved[0]["access_token"] == "new-access"
    assert fake.verified == "654321"
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    assert pool[0]["status"] == "registered"
    assert success["status"] == "success"


def test_phone_change_pool_failure_releases_reserved_row(monkeypatch, tmp_path):
    accounts_path = tmp_path / "accounts.json"
    pool_path = tmp_path / "pool.json"
    monkeypatch.setenv("OPAI_GOPAY_ACCOUNTS_FILE", str(accounts_path))
    accounts_path.write_text(json.dumps([{"phone": "+6281234567890", "local": "81234567890"}]), encoding="utf-8")
    pool_path.write_text(json.dumps([{"phone": "+6281234567891", "sms_url": "", "status": "available"}]), encoding="utf-8")

    from opai.core.payment_inbox import _PhoneChangeManager

    class FailingClient(FakeClient):
        def phone_change_request(self, phone, country_code="+62"):
            return {"status": 404, "body": {"error": "fixture"}}

    manager = _PhoneChangeManager(pool_path=pool_path, client_factory=lambda account, phone: FailingClient())
    created = manager.start(phone="+6281234567890", source="pool", replacement_phone="+6281234567891")
    failed = _wait_for(manager, created["id"], "failed")
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    assert pool[0]["status"] == "available"
    assert "申请失败" in failed["message"]


def test_phone_change_provider_source_auto_reads_configured_sms(monkeypatch, tmp_path):
    accounts_path = tmp_path / "accounts.json"
    monkeypatch.setenv("OPAI_GOPAY_ACCOUNTS_FILE", str(accounts_path))
    accounts_path.write_text(json.dumps([{"phone": "+6281234567890", "local": "81234567890"}]), encoding="utf-8")

    from opai.core import payment_inbox

    fake = FakeClient()
    monkeypatch.setattr(payment_inbox, "_gopay_sms_api_key", lambda provider: "provider-key")
    monkeypatch.setattr(payment_inbox, "_gopay_sms_get_number", lambda provider: ("+6281234567892", "activation-1"))
    monkeypatch.setattr(payment_inbox, "_gopay_sms_wait_code", lambda *args, **kwargs: "7777")
    manager = payment_inbox._PhoneChangeManager(client_factory=lambda account, phone: fake)
    created = manager.start(phone="+6281234567890", source="smspool")
    success = _wait_for(manager, created["id"], "success")

    saved = json.loads(accounts_path.read_text(encoding="utf-8"))
    assert saved[0]["phone"] == "+6281234567892"
    assert saved[0]["sms_provider"] == "smspool"
    assert saved[0]["activation_id"] == "activation-1"
    assert fake.verified == "7777"
    assert success["provider"] == "smspool"
