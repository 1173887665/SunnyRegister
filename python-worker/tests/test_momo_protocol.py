from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from momo_runtime.app.src.momo_core.momo_manager import MomoManager
from momo_runtime.app.src.momo_core.momo_protocol import DirectMomoProvider
from momo_runtime.app.src.momo_core import momo_manager as momo_manager_module
from momo_runtime.app.src.momo_core.momo_sms_provider import SmsBowerProvider, SmsLease


class _ProtocolHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict, dict[str, str], bytes]] = []

    def do_GET(self) -> None:  # noqa: N802
        raw = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size) or b"{}"
        payload = json.loads(body)
        headers = {key.lower(): value for key, value in self.headers.items()}
        self.__class__.calls.append((self.path, payload, headers, body))
        response: dict = {"ok": True}
        if self.path == "/register/start":
            response.update({"phone": payload.get("phone"), "session": "register-session"})
        elif self.path == "/register/send-otp":
            response["otp_sent"] = True
        elif self.path == "/register/verify-otp":
            response["otp_verified"] = True
        elif self.path == "/register/profile":
            response["kyc_status"] = "skipped"
        elif self.path == "/register/pin":
            response["pin_set"] = True
        elif self.path == "/device/bind":
            response.update({"device_bound": True, "device_id": "device-1"})
        elif self.path == "/session":
            response.update({"session_ready": True, "session": "session-1"})
        elif self.path in {"/login", "/v1/auth/login"}:
            response = {"success": True, "data": {"session": "login-session", "session_ready": True}}
        elif self.path == "/payment/scan":
            response.update({"requires_otp": True, "payment_token": "payment-token"})
        elif self.path == "/payment/otp":
            response.update({"requires_confirmation": True, "confirmation_token": "confirm-token"})
        elif self.path == "/payment/confirm":
            response.update({"payment_id": "payment-1"})
        else:
            response = {"ok": False, "error": "unknown route"}
        raw = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args: object) -> None:
        return


def _server() -> ThreadingHTTPServer:
    _ProtocolHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProtocolHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wait(manager: MomoManager, job_id: str, statuses: set[str], timeout: float = 5) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.get_job(job_id) or {}
        if str(job.get("status")) in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(manager.get_job(job_id))


def test_direct_provider_sends_wallet_operations_without_adapter() -> None:
    server = _server()
    try:
        provider = DirectMomoProvider(f"http://127.0.0.1:{server.server_port}", headers={"X-Test": "1"})
        assert provider.start_register("+84901234567").ok
        assert provider.send_otp({"phone": "+84901234567"}).ok
        assert provider.verify_otp({"phone": "+84901234567"}, "123456").ok
        assert provider.submit_payment_otp({"phone": "+84901234567"}, "654321").ok
        assert provider.confirm_payment({"phone": "+84901234567"}).ok
        assert [path for path, *_ in _ProtocolHandler.calls] == [
            "/register/start", "/register/send-otp", "/register/verify-otp", "/payment/otp", "/payment/confirm"
        ]
        assert all(call[2]["x-test"] == "1" for call in _ProtocolHandler.calls)
        assert all(call[2].get("idempotency-key") for call in _ProtocolHandler.calls)
    finally:
        server.shutdown()
        server.server_close()


def test_direct_provider_normalizes_nested_response_and_custom_route() -> None:
    server = _server()
    try:
        provider = DirectMomoProvider(
            f"http://127.0.0.1:{server.server_port}",
            routes={"login": "/v1/auth/login"},
        )
        result = provider.login("+84901234567", "1234")
        assert result.ok
        assert result.data["session"] == "login-session"
        assert _ProtocolHandler.calls[0][0] == "/v1/auth/login"
    finally:
        server.shutdown()
        server.server_close()


def test_direct_provider_uses_static_then_session_bearer_token() -> None:
    server = _server()
    try:
        provider = DirectMomoProvider(
            f"http://127.0.0.1:{server.server_port}",
            auth_mode="bearer",
            token="service-token",
        )
        assert provider.start_register("+84901234567").ok
        assert provider.get_session({"token": "wallet-session"}).ok
        assert _ProtocolHandler.calls[0][2]["authorization"] == "Bearer service-token"
        assert _ProtocolHandler.calls[1][2]["authorization"] == "Bearer wallet-session"
    finally:
        server.shutdown()
        server.server_close()


def test_direct_provider_hmac_signs_body_and_propagates_device() -> None:
    server = _server()
    try:
        provider = DirectMomoProvider(
            f"http://127.0.0.1:{server.server_port}",
            auth_mode="hmac_sha256",
            access_key="access-1",
            secret_key="secret-1",
        )
        result = provider.confirm_payment({"request_id": "request-1", "device_id": "device-1"})
        assert result.ok
        path, _payload, headers, body = _ProtocolHandler.calls[0]
        canonical = "\n".join([
            "POST", path, headers["x-timestamp"], headers["x-nonce"], hashlib.sha256(body).hexdigest(),
        ])
        expected = hmac.new(b"secret-1", canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        assert headers["x-access-key"] == "access-1"
        assert headers["x-device-id"] == "device-1"
        assert headers["x-signature"] == expected
        assert headers["idempotency-key"] == "request-1"
    finally:
        server.shutdown()
        server.server_close()


def test_manager_handles_direct_protocol_payment_otp_and_confirmation(tmp_path: Path) -> None:
    server = _server()
    try:
        manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
        manager.update_settings({"protocol_base_url": f"http://127.0.0.1:{server.server_port}", "mock_mode": False})
        manager.import_phones("+84901234567----https://example.test/sms/1")
        registration = manager.start_register(pin="1234")
        _wait(manager, registration["id"], {"waiting_otp"})
        manager.submit_otp(registration["id"], "123456")
        _wait(manager, registration["id"], {"success"})

        payment = manager.start_payment(phone="+84901234567", qr_payload="momo://merchant/1", amount="50000")
        _wait(manager, payment["id"], {"waiting_otp"})
        manager.submit_otp(payment["id"], "654321")
        _wait(manager, payment["id"], {"awaiting_confirmation"})
        manager.confirm_payment(payment["id"])
        finished = _wait(manager, payment["id"], {"success"})
        assert finished["payment_id"] == "payment-1"
        paths = [path for path, *_ in _ProtocolHandler.calls]
        assert paths[:7] == [
            "/register/start", "/register/send-otp", "/register/verify-otp",
            "/register/profile", "/register/pin", "/device/bind", "/session",
        ]
        assert paths[7:13] == [
            "/login", "/device/bind", "/session", "/payment/scan", "/payment/otp", "/payment/confirm",
        ]
    finally:
        server.shutdown()
        server.server_close()


def test_manager_auto_confirms_payment_after_protocol_otp(tmp_path: Path) -> None:
    server = _server()
    try:
        manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
        manager.update_settings({"protocol_base_url": f"http://127.0.0.1:{server.server_port}", "mock_mode": False})
        manager.import_phones("+84901234567----https://example.test/sms/1")
        registration = manager.start_register(pin="1234")
        _wait(manager, registration["id"], {"waiting_otp"})
        manager.submit_otp(registration["id"], "123456")
        _wait(manager, registration["id"], {"success"})

        payment = manager.start_payment(
            phone="+84901234567", qr_payload="momo://merchant/auto", amount="50000", auto_confirm=True,
        )
        _wait(manager, payment["id"], {"waiting_otp"})
        manager.submit_otp(payment["id"], "654321")
        finished = _wait(manager, payment["id"], {"success"})
        assert finished["protocol_stage"] == "completed"
        assert finished["automation_mode"] == "automatic"
        assert finished["requires_user_action"] is False
        assert finished["payment_id"] == "payment-1"
        assert [path for path, *_ in _ProtocolHandler.calls][-1] == "/payment/confirm"
    finally:
        server.shutdown()
        server.server_close()


def test_manager_resumes_auto_confirmation_after_restart(tmp_path: Path) -> None:
    server = _server()
    try:
        state = tmp_path / "state.json"
        state.write_text(json.dumps({
            "jobs": {
                "recovered-payment": {
                    "id": "recovered-payment", "kind": "payment", "phone": "+84901234567",
                    "pin": "1234", "proxy": "", "status": "awaiting_confirmation", "stage": "confirm",
                    "protocol_stage": "confirmation", "automation_mode": "automatic",
                    "requires_user_action": False, "next_action": "confirm_payment", "auto_confirm": True,
                    "_session": {"phone": "+84901234567", "session": "session-1"},
                    "_payment_context": {"payment_token": "payment-token"}, "logs": [],
                    "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                },
            },
            "accounts": {"+84901234567": {"phone": "+84901234567", "pin": "1234"}},
            "settings": {"protocol_base_url": f"http://127.0.0.1:{server.server_port}", "mock_mode": False},
        }), encoding="utf-8")
        manager = MomoManager(state_file=str(state), pool_file=str(tmp_path / "pool.json"))
        finished = _wait(manager, "recovered-payment", {"success"})
        assert finished["protocol_stage"] == "completed"
        assert finished["payment_id"] == "payment-1"
        assert [path for path, *_ in _ProtocolHandler.calls][-1] == "/payment/confirm"
    finally:
        server.shutdown()
        server.server_close()


def test_manager_cancel_marks_protocol_stage(tmp_path: Path) -> None:
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    manager.import_phones("+84901234567----https://sms.example.test/activation/1")
    created = manager.start_register(pin="1234")
    cancelled = manager.cancel_job(created["id"])
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["protocol_stage"] == "cancelled"
    assert cancelled["requires_user_action"] is False
    assert cancelled["next_action"] == ""


def test_manager_polls_pool_otp_without_user_submission(tmp_path: Path) -> None:
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    manager.import_phones("+84901234567----https://sms.example.test/activation/1")
    manager._poll_pool_code = lambda _phone, _proxy="", _ignored=None: "123456"  # type: ignore[method-assign]
    created = manager.start_register(pin="1234")
    finished = _wait(manager, created["id"], {"success"})
    assert finished["protocol_stage"] == "completed"
    assert finished["requires_user_action"] is False
    assert finished["next_action"] == ""


def test_manager_migrates_saved_adapter_fields_to_direct_protocol(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"jobs": {}, "accounts": {}, "settings": {
        "api_base_url": "https://protocol.example/api",
        "api_headers_json": '{"X-Legacy":"1"}',
    }}), encoding="utf-8")
    manager = MomoManager(state_file=str(state), pool_file=str(tmp_path / "pool.json"))
    settings = manager.get_settings()
    assert settings["protocol_base_url"] == "https://protocol.example/api"
    assert settings["protocol_headers_configured"] is True
    assert "api_base_url" not in settings
    assert settings["runtime_version"] == "direct-protocol-v1"
    assert manager.check_settings()["runtime_version"] == "direct-protocol-v1"
    assert all(item["name"] != "momo_adapter" for item in manager.check_settings()["checks"])


def test_manager_masks_direct_protocol_credentials_and_checks_auth(tmp_path: Path) -> None:
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    settings = manager.update_settings({
        "protocol_base_url": "http://127.0.0.1:9/api",
        "protocol_auth_mode": "hmac_sha256",
        "protocol_access_key": "access-secret",
        "protocol_secret_key": "signing-secret",
    })
    assert settings["protocol_access_key"] != "access-secret"
    assert settings["protocol_secret_key_configured"] is True
    check = manager.check_settings()
    auth = next(item for item in check["checks"] if item["name"] == "protocol_auth")
    assert auth == {"name": "protocol_auth", "ok": True, "message": "HMAC-SHA256 密钥已配置"}


def test_manager_rejects_manual_registration_phone_when_sms_source_is_external(tmp_path: Path) -> None:
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    manager.update_settings({"phone_source": "smsbower", "sms_api_key": "key"})
    try:
        manager.start_register(phone="+84901234567", pin="1234")
    except ValueError as exc:
        assert "系统配置" in str(exc)
    else:
        raise AssertionError("manual phone must not bypass external SMS source")


def test_manager_uses_embedded_protocol_when_endpoint_is_not_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPAI_MOMO_MOCK_MODE", "0")
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    settings = manager.get_settings()
    assert settings["provider_mode"] == "embedded"
    assert settings["live_protocol_ready"] is False
    assert [item["value"] for item in settings["sms_sources"]] == ["pool", "smsbower", "smspool", "grizzlysms", "hero_sms"]
    check = manager.check_settings()
    protocol = next(item for item in check["checks"] if item["name"] == "momo_protocol")
    assert protocol == {"name": "momo_protocol", "ok": True, "message": "系统内置默认协议"}

    manager.import_phones("+84901234567----https://example.test/sms/1")
    registration = manager.start_register(pin="1234")
    _wait(manager, registration["id"], {"waiting_otp"})
    manager.submit_otp(registration["id"], "123456")
    assert _wait(manager, registration["id"], {"success"})["status"] == "success"


def test_manager_rejects_embedded_provider_in_production(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPAI_MOMO_MOCK_MODE", raising=False)
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    manager._mock_mode_explicit = False
    manager.import_phones("+84901234567----https://example.test/sms/1")
    check = manager.check_settings()
    protocol = next(item for item in check["checks"] if item["name"] == "momo_protocol")
    assert protocol["ok"] is False
    try:
        manager.start_register(pin="1234")
    except ValueError as exc:
        assert "直连协议" in str(exc)
    else:
        raise AssertionError("production mode must require a live protocol endpoint")


def test_provider_state_does_not_treat_empty_success_as_paid() -> None:
    from momo_runtime.app.src.momo_core.momo_models import ProviderResult

    assert MomoManager._provider_state(ProviderResult(True, {})) == "unknown"
    assert MomoManager._provider_state(ProviderResult(True, {"payment_token": "token-1"})) == "awaiting_confirmation"
    assert MomoManager._provider_state(ProviderResult(True, {"payment_id": "payment-1"})) == "success"


def test_sms_supplier_control_plane_does_not_inherit_wallet_proxy(tmp_path: Path, monkeypatch) -> None:
    captured: list[dict] = []

    class _SmsProvider:
        def acquire(self) -> SmsLease:
            return SmsLease("smsbower", "+84901234567", "activation-1", "key-1")

    def fake_provider(settings: dict) -> _SmsProvider:
        captured.append(dict(settings))
        return _SmsProvider()

    monkeypatch.setattr(momo_manager_module, "build_sms_provider", fake_provider)
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    manager.update_settings({"phone_source": "smsbower", "sms_api_key": "key-1"})
    with manager.lock:
        manager.settings["sms_proxy"] = "http://wallet-proxy.example:8080"

    lease = manager._acquire_sms_lease("smsbower")
    assert lease.phone == "+84901234567"
    assert captured[0].get("sms_proxy", "") == ""


def test_smsbower_maps_momo_display_defaults_to_supplier_codes(monkeypatch) -> None:
    captured: dict = {}
    provider = SmsBowerProvider({"sms_api_key": "key-1", "sms_service_code": "momo", "sms_country_code": "84"})

    def fake_call(action: str, **params):
        captured.update({"action": action, **params})
        return "ACCESS_NUMBER:activation-1:84901234567"

    monkeypatch.setattr(provider, "_call", fake_call)
    lease = provider.acquire()
    assert lease.phone == "+84901234567"
    assert captured == {"action": "getNumber", "service": "hc", "country": "10"}
