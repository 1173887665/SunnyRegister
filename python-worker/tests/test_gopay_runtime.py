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
        assert accounts == {"accounts": []}
        assert phones == {"phones": []}
    finally:
        httpd.shutdown()
        httpd.server_close()
