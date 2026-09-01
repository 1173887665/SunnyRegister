from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from momo_runtime.app.src.momo_core.momo_manager import MomoManager
from momo_runtime.app.src.momo_core.momo_protocol import HttpMomoProvider
from momo_adapter.server import Adapter


class _AdapterHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict]] = []

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size) or b"{}")
        self.__class__.calls.append((self.path, payload))
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
        elif self.path == "/login":
            response.update({"session": "login-session", "session_ready": True})
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


def _wait(manager: MomoManager, job_id: str, statuses: set[str], timeout: float = 5) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = manager.get_job(job_id) or {}
        if str(job.get("status")) in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(manager.get_job(job_id))


def test_http_provider_sends_all_wallet_operations_and_proxy() -> None:
    _AdapterHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AdapterHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = HttpMomoProvider(f"http://127.0.0.1:{server.server_port}", headers={"X-Test": "1"})
        assert provider.start_register("+84901234567").ok
        assert provider.send_otp({"phone": "+84901234567"}).ok
        assert provider.verify_otp({"phone": "+84901234567"}, "123456").ok
        assert provider.submit_payment_otp({"phone": "+84901234567"}, "654321").ok
        assert provider.confirm_payment({"phone": "+84901234567"}).ok
        assert [path for path, _ in _AdapterHandler.calls] == [
            "/register/start", "/register/send-otp", "/register/verify-otp", "/payment/otp", "/payment/confirm"
        ]
    finally:
        server.shutdown()


def test_http_provider_requires_explicit_ok_contract() -> None:
    class MissingOkHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            raw = b'{"session":"not-enough"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), MissingOkHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = HttpMomoProvider(f"http://127.0.0.1:{server.server_port}").login("+84901234567", "1234")
        assert not result.ok
        assert "ok" in result.error
    finally:
        server.shutdown()


def test_manager_handles_payment_otp_and_confirmation(tmp_path: Path) -> None:
    _AdapterHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AdapterHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
        manager.update_settings({"api_base_url": f"http://127.0.0.1:{server.server_port}", "mock_mode": False})
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
        paths = [path for path, _ in _AdapterHandler.calls]
        assert paths[:7] == [
            "/register/start", "/register/send-otp", "/register/verify-otp",
            "/register/profile", "/register/pin", "/device/bind", "/session",
        ]
        assert paths[7:12] == ["/login", "/device/bind", "/session", "/payment/scan", "/payment/otp"]
    finally:
        server.shutdown()


def test_manager_rejects_manual_registration_phone_when_sms_source_is_external(tmp_path: Path) -> None:
    manager = MomoManager(state_file=str(tmp_path / "state.json"), pool_file=str(tmp_path / "pool.json"))
    manager.update_settings({"phone_source": "smsbower", "sms_api_key": "key"})
    try:
        manager.start_register(phone="+84901234567", pin="1234")
    except ValueError as exc:
        assert "系统配置" in str(exc)
    else:
        raise AssertionError("manual phone must not bypass external SMS source")


def test_embedded_adapter_forwards_configured_headers_and_token(monkeypatch) -> None:
    received: list[dict[str, str]] = []

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"ok":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            received.append({key.lower(): value for key, value in self.headers.items()})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"ok":true,"payment_id":"upstream-1"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("OPAI_MOMO_ADAPTER_UPSTREAM_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("OPAI_MOMO_ADAPTER_HEADERS", '{"X-Upstream":"fixture"}')
        monkeypatch.setenv("OPAI_MOMO_ADAPTER_TOKEN", "adapter-secret")
        adapter = Adapter()
        health = adapter.health()
        assert health["ok"] is True
        status, result = adapter.call("/payment/confirm", {"request_id": "request-1"})
        assert status == 200 and result["payment_id"] == "upstream-1"
        assert received[0]["x-upstream"] == "fixture"
        assert received[0]["authorization"] == "Bearer adapter-secret"
        assert received[0]["idempotency-key"] == "request-1"
    finally:
        server.shutdown()
