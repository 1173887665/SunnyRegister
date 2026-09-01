from __future__ import annotations

import json
import urllib.request


def test_gopay_embedded_service_uses_configured_phone_pool(monkeypatch, tmp_path):
    pool_path = tmp_path / "phone_pool.json"
    monkeypatch.setenv("OPAI_GOPAY_PHONE_POOL_FILE", str(pool_path))
    monkeypatch.setenv("OPAI_GOPAY_ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    monkeypatch.setenv("OPAI_GOPAY_SMS_ENV_FILE", str(tmp_path / "sms.env"))

    from gopay_runtime.gopay import server as gopay_server

    assert gopay_server.POOL == pool_path
    httpd = gopay_server.start_embedded()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        accounts = json.loads(urllib.request.urlopen(base_url + "/api/accounts", timeout=5).read())
        phones = json.loads(urllib.request.urlopen(base_url + "/api/phone-pool", timeout=5).read())
        sms_status = json.loads(urllib.request.urlopen(base_url + "/api/sms-status", timeout=5).read())
        assert accounts == {"accounts": []}
        assert phones == {"phones": []}
        assert set(sms_status["providers"]) == {"smsbower", "smspool", "grizzlysms", "hero_sms"}

        request = urllib.request.Request(
            base_url + "/api/sms-config",
            data=json.dumps({
                "provider": "hero_sms",
                "api_key": "hero-test-key",
                "api_base_url": "https://hero-sms.com/api/v1",
                "service": "dr",
                "country": "6",
                "max_price": "0.5",
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        saved = json.loads(urllib.request.urlopen(request, timeout=5).read())
        assert saved["provider"] == "hero_sms"
        assert saved["api_key_configured"] is True

        refreshed = json.loads(urllib.request.urlopen(base_url + "/api/sms-status", timeout=5).read())
        assert refreshed["providers"]["hero_sms"]["api_key_configured"] is True
        assert refreshed["providers"]["hero_sms"]["max_price"] == "0.5"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_gopay_batch_job_exposes_latest_failure_reason(monkeypatch, tmp_path):
    monkeypatch.setenv("OPAI_GOPAY_PHONE_POOL_FILE", str(tmp_path / "phone_pool.json"))
    monkeypatch.setenv("OPAI_GOPAY_ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    monkeypatch.setenv("OPAI_GOPAY_SMS_ENV_FILE", str(tmp_path / "sms.env"))

    from gopay_runtime.gopay import server as gopay_server

    batch_id = "batch-with-error"
    with gopay_server.batch_jobs_lock:
        gopay_server.batch_jobs[batch_id] = {
            "id": batch_id,
            "count": 1,
            "started": 1,
            "source": "hero_sms",
            "status": "failed",
            "message": "完成 1/1，成功 0，失败 1",
            "last_error": "注册 OTP 申请失败: HTTP 429",
        }

    httpd = gopay_server.start_embedded()
    try:
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        payload = json.loads(urllib.request.urlopen(base_url + "/api/register-jobs", timeout=5).read())
        batch = next(item for item in payload["jobs"] if item["id"] == batch_id)
        assert batch["message"] == "完成 1/1，成功 0，失败 1；原因：注册 OTP 申请失败: HTTP 429"
    finally:
        httpd.shutdown()
        httpd.server_close()
        with gopay_server.batch_jobs_lock:
            gopay_server.batch_jobs.pop(batch_id, None)
