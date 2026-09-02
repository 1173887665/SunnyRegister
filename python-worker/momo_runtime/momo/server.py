"""Standalone HTTP service for MoMo registration and QR payments."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "app" / "src"))

from momo_core.momo_manager import MomoManager  # noqa: E402


manager = MomoManager()


def _body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        value = json.loads(handler.rfile.read(size) or b"{}") if size else {}
        return value if isinstance(value, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def _job_id(path: str, prefix: str) -> str:
    return unquote(path[len(prefix):].strip("/"))


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, value: object) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/accounts":
            return self.send_json(200, {"accounts": manager.list_accounts()})
        if path == "/api/phone-pool":
            return self.send_json(200, {"phones": manager.list_phones()})
        if path == "/api/register-jobs":
            return self.send_json(200, {"jobs": manager.list_jobs("register")})
        if path == "/api/payment-jobs":
            return self.send_json(200, {"jobs": manager.list_jobs("payment")})
        if path == "/api/settings":
            return self.send_json(200, manager.get_settings())
        if path == "/api/settings/check":
            return self.send_json(200, manager.check_settings())
        if path.startswith("/api/register-jobs/"):
            job = manager.get_job(_job_id(path, "/api/register-jobs/"))
            return self.send_json(200, job) if job else self.send_json(404, {"error": "momo_register_job_not_found"})
        if path.startswith("/api/payment-jobs/"):
            job = manager.get_job(_job_id(path, "/api/payment-jobs/"))
            return self.send_json(200, job) if job else self.send_json(404, {"error": "momo_payment_job_not_found"})
        return self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        data = _body(self)
        try:
            if path == "/api/register":
                count = min(100, max(1, int(data.get("count") or 1)))
                login_existing = data.get("login_existing") is True or str(data.get("login_existing") or "").strip().lower() in {"1", "true", "yes", "on"}
                if login_existing and count != 1:
                    raise ValueError("登录已有账号一次只能提交一个手机号")
                jobs = []
                for index in range(count):
                    jobs.append(manager.start_register(
                        phone=str(data.get("phone") or "") if index == 0 else "",
                        source=str(data.get("source") or "pool"),
                        pin=str(data.get("pin") or ""),
                        login_existing=login_existing,
                        skip_kyc=data.get("skip_kyc"),
                        proxy=str(data.get("proxy") or ""),
                        profile=data.get("profile") if isinstance(data.get("profile"), dict) else None,
                    ))
                return self.send_json(201, jobs[0] if count == 1 else {"id": f"batch-{jobs[0].get('id')}", "status": "running", "jobs": jobs, "count": count})
            if path.startswith("/api/register-jobs/") and path.endswith("/otp"):
                job = manager.submit_otp(_job_id(path[:-4], "/api/register-jobs/"), str(data.get("code") or data.get("value") or ""))
                return self.send_json(200, job) if job else self.send_json(400, {"error": "任务不在等待 OTP 状态"})
            if path.startswith("/api/register-jobs/") and path.endswith("/cancel"):
                job = manager.cancel_job(_job_id(path[:-7], "/api/register-jobs/"))
                return self.send_json(200, job) if job else self.send_json(400, {"error": "注册任务已结束或不存在"})
            if path == "/api/payment":
                raw_auto_confirm = data.get("auto_confirm")
                auto_confirm = None if raw_auto_confirm is None else str(raw_auto_confirm).strip().lower() in {"1", "true", "yes", "on"}
                job = manager.start_payment(
                    phone=str(data.get("phone") or ""),
                    qr_payload=str(data.get("qr_payload") or ""),
                    amount=str(data.get("amount") or ""),
                    pin=str(data.get("pin") or ""),
                    proxy=str(data.get("proxy") or ""),
                    auto_confirm=auto_confirm,
                )
                return self.send_json(201, job)
            if path.startswith("/api/payment-jobs/") and path.endswith("/otp"):
                job = manager.submit_otp(_job_id(path[:-4], "/api/payment-jobs/"), str(data.get("code") or data.get("value") or ""))
                return self.send_json(200, job) if job else self.send_json(400, {"error": "支付任务不在等待 OTP 状态"})
            if path.startswith("/api/payment-jobs/") and path.endswith("/confirm"):
                job = manager.confirm_payment(_job_id(path[:-8], "/api/payment-jobs/"))
                return self.send_json(202, job) if job else self.send_json(400, {"error": "支付任务不在等待确认状态"})
            if path.startswith("/api/payment-jobs/") and path.endswith("/cancel"):
                job = manager.cancel_job(_job_id(path[:-7], "/api/payment-jobs/"))
                return self.send_json(200, job) if job else self.send_json(400, {"error": "支付任务已结束或不存在"})
            if path == "/api/phone-pool/import":
                return self.send_json(201, manager.import_phones(str(data.get("text") or "")))
            if path == "/api/phone-pool/clear":
                return self.send_json(200, {"ok": True, "removed": manager.clear_phones()})
            if path == "/api/phone-pool/delete":
                phone = str(data.get("phone") or "")
                if not manager.delete_phone(phone):
                    return self.send_json(404, {"error": "号码不存在"})
                return self.send_json(200, {"ok": True, "phone": phone})
            if path == "/api/settings":
                return self.send_json(200, manager.update_settings(data))
            if path == "/api/settings/check":
                return self.send_json(200, manager.check_settings(data))
            if path.startswith("/api/accounts/") and path.endswith("/relogin"):
                phone = unquote(path[len("/api/accounts/"):-len("/relogin")].strip("/"))
                return self.send_json(201, manager.relogin(phone))
            if path.startswith("/api/accounts/") and path.endswith("/delete"):
                phone = unquote(path[len("/api/accounts/"):-len("/delete")].strip("/"))
                if not manager.delete_account(phone):
                    return self.send_json(404, {"error": "账号不存在"})
                return self.send_json(200, {"ok": True, "phone": phone})
            return self.send_json(404, {"error": "not_found"})
        except Exception as exc:
            return self.send_json(400, {"error": re.sub(r"\s+", " ", str(exc))[:500]})

    def log_message(self, *_args: object) -> None:
        return


def start_embedded() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    import threading
    threading.Thread(target=server.serve_forever, name="momo-http", daemon=True).start()
    return server


def main() -> None:
    ThreadingHTTPServer(("127.0.0.1", 19081), Handler).serve_forever()


if __name__ == "__main__":
    main()
