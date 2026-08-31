"""Standalone MoMo task manager.

The manager owns its own state files and provider boundary.  The default local
mode exercises the complete registration, OTP and QR-payment state machine
without touching a live payment service.  A provider adapter can be enabled by
setting ``api_base_url`` in the MoMo settings endpoint.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .momo_protocol import build_provider
from .momo_worker import MomoTaskWorker


ROOT = Path(__file__).resolve().parents[5]
DATA_ROOT = Path(os.getenv("SUNNY_DATA_DIR") or ("/app/data" if Path("/app/data").is_dir() else ROOT / "data"))
MOMO_ROOT = DATA_ROOT / "momo"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("0084"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = "84" + digits[1:]
    if digits.startswith("84") and 9 <= len(digits[2:]) <= 10:
        return f"+{digits}"
    return ""


def _valid_pin(value: str) -> bool:
    return not value or bool(re.fullmatch(r"\d{4,8}", value))


def _mask_proxy(value: str) -> str:
    return re.sub(r"(://)([^/@]+)@", r"\1***@", str(value or ""))


class MomoManager:
    """Persistent, provider-independent registration and QR payment manager."""

    def __init__(self, state_file: str | None = None, pool_file: str | None = None) -> None:
        MOMO_ROOT.mkdir(parents=True, exist_ok=True)
        self.state_file = Path(state_file or os.getenv("OPAI_MOMO_STATE_FILE", MOMO_ROOT / "state.json"))
        self.pool_file = Path(pool_file or os.getenv("OPAI_MOMO_PHONE_POOL_FILE", MOMO_ROOT / "phone_pool.json"))
        self.lock = threading.RLock()
        self.conds: dict[str, threading.Condition] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.accounts: dict[str, dict[str, Any]] = {}
        self.phones: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] = {
            "api_base_url": os.getenv("OPAI_MOMO_API_BASE_URL", "").strip(),
            "mock_mode": os.getenv("OPAI_MOMO_MOCK_MODE", "1").strip().lower() not in {"0", "false", "no"},
            "skip_kyc_default": True,
        }
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.jobs = payload.get("jobs") if isinstance(payload.get("jobs"), dict) else {}
                self.accounts = payload.get("accounts") if isinstance(payload.get("accounts"), dict) else {}
                saved = payload.get("settings")
                if isinstance(saved, dict):
                    self.settings.update(saved)
        except (OSError, ValueError):
            pass
        try:
            rows = json.loads(self.pool_file.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                self.phones = {str(row.get("phone")): dict(row) for row in rows if isinstance(row, dict) and row.get("phone")}
        except (OSError, ValueError):
            pass

    def _persist(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": self.jobs, "accounts": self.accounts, "settings": self.settings}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)
        self.pool_file.parent.mkdir(parents=True, exist_ok=True)
        pool_tmp = self.pool_file.with_suffix(".tmp")
        pool_tmp.write_text(json.dumps(list(self.phones.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        pool_tmp.replace(self.pool_file)

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        clean = {key: value for key, value in job.items() if not key.startswith("_")}
        clean.pop("pin", None)
        clean.pop("proxy", None)
        return clean

    def _public_account(self, account: dict[str, Any]) -> dict[str, Any]:
        clean = dict(account)
        clean.pop("pin", None)
        clean.pop("session", None)
        clean.pop("proxy", None)
        return clean

    def list_jobs(self, kind: str | None = None) -> list[dict[str, Any]]:
        with self.lock:
            rows = [self._public_job(row) for row in self.jobs.values() if not kind or row.get("kind") == kind]
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(str(job_id))
            return self._public_job(job) if job else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = [self._public_account(row) for row in self.accounts.values()]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return rows

    def list_phones(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.phones.values()]

    def get_settings(self) -> dict[str, Any]:
        with self.lock:
            return {"api_base_url": self.settings.get("api_base_url", ""), "mock_mode": bool(self.settings.get("mock_mode", True)), "skip_kyc_default": bool(self.settings.get("skip_kyc_default", True))}

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if "api_base_url" in values:
                self.settings["api_base_url"] = str(values.get("api_base_url") or "").strip().rstrip("/")
            if "mock_mode" in values:
                self.settings["mock_mode"] = bool(values.get("mock_mode"))
            if "skip_kyc_default" in values:
                self.settings["skip_kyc_default"] = bool(values.get("skip_kyc_default"))
            self._persist()
            return self.get_settings()

    def import_phones(self, raw: str) -> dict[str, int]:
        inserted = 0
        with self.lock:
            for line in str(raw or "").splitlines():
                if "----" not in line:
                    continue
                phone_raw, sms_url = [item.strip() for item in line.split("----", 1)]
                phone = _normalize_phone(phone_raw)
                if not phone or phone in self.phones:
                    continue
                self.phones[phone] = {"phone": phone, "sms_url": sms_url, "status": "available", "country": "VN", "created_at": _now()}
                inserted += 1
            self._persist()
            return {"inserted": inserted, "total": len(self.phones)}

    def clear_phones(self) -> int:
        with self.lock:
            removed = len(self.phones)
            self.phones.clear()
            self._persist()
            return removed

    def delete_phone(self, phone: str) -> bool:
        normalized = _normalize_phone(phone)
        with self.lock:
            existed = normalized in self.phones
            self.phones.pop(normalized, None)
            if existed:
                self._persist()
            return existed

    def _allocate_phone(self, requested: str) -> str:
        normalized = _normalize_phone(requested)
        if normalized:
            return normalized
        for phone, row in self.phones.items():
            if str(row.get("status") or "available") == "available":
                row["status"] = "reserved"
                return phone
        raise ValueError("没有可用的 +84 越南号码")

    def _new_job(self, *, kind: str, phone: str, pin: str, proxy: str, **values: Any) -> tuple[str, threading.Condition]:
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        condition = threading.Condition(self.lock)
        self.conds[job_id] = condition
        self.jobs[job_id] = {
            "id": job_id, "kind": kind, "phone": phone, "pin": pin, "proxy": proxy,
            "status": "running", "message": "任务已创建", "created_at": now, "updated_at": now,
            "logs": [], "_otp": [], "_otp_deadline": 0, **values,
        }
        self._log(job_id, "任务已创建")
        return job_id, condition

    def _log(self, job_id: str, message: str) -> None:
        job = self.jobs.get(job_id)
        if not job:
            return
        job.setdefault("logs", []).append({"at": _now(), "message": message})
        job["message"] = message
        job["updated_at"] = _now()
        self._persist()

    def start_register(self, *, phone: str = "", source: str = "pool", pin: str = "", login_existing: bool = False, skip_kyc: bool | None = None, proxy: str = "") -> dict[str, Any]:
        pin = str(pin or "").strip()
        if not _valid_pin(pin):
            raise ValueError("支付密码请输入 4 到 8 位数字")
        normalized = self._allocate_phone(phone)
        with self.lock:
            existing = self.accounts.get(normalized)
            if login_existing and not existing:
                raise ValueError("该手机号尚未在 MoMo 账号库中")
            if not login_existing and existing:
                raise ValueError("该手机号已经注册，请使用登录已有号")
            job_id, _ = self._new_job(kind="register", phone=normalized, pin=pin, proxy=proxy, source=source, login_existing=bool(login_existing), skip_kyc=self.settings.get("skip_kyc_default", True) if skip_kyc is None else bool(skip_kyc), country="VN")
        threading.Thread(target=self._run_register, args=(job_id,), daemon=True, name=f"momo-register-{job_id}").start()
        return self.get_job(job_id) or {}

    def _run_register(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            self._log(job_id, "初始化 MoMo +84 设备会话")
            job["status"] = "waiting_otp"
            job["_otp_deadline"] = time.time() + 300
            self._log(job_id, "等待 MoMo 注册或登录 OTP")
            condition = self.conds[job_id]
            last_sms_poll = 0.0
            while not job["_otp"] and time.time() < job["_otp_deadline"]:
                if job.get("source") == "pool" and time.time() - last_sms_poll >= 2:
                    last_sms_poll = time.time()
                    code = self._poll_pool_code(str(job.get("phone") or ""))
                    if code:
                        job.setdefault("_otp", []).append(code)
                        self._log(job_id, "已从号码池短信接口读取 OTP")
                        break
                condition.wait(timeout=1)
            if not job["_otp"]:
                job["status"] = "failed"
                self._log(job_id, "OTP 等待超时")
                return
            code = str(job["_otp"].pop(0))
            self._log(job_id, f"OTP 已提交（{len(code)} 位）")
            job["status"] = "running"
        if not self._provider_step("register", job_id, {"phone": job["phone"], "otp": code, "skip_kyc": job.get("skip_kyc", True)}):
            return
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            now = _now()
            if job.get("login_existing"):
                account = self.accounts.setdefault(job["phone"], {})
                account.update({"phone": job["phone"], "status": "registered", "session_ready": True, "last_login_at": now, "updated_at": now, "kyc_status": account.get("kyc_status", "skipped")})
                self._log(job_id, "MoMo 已登录，账号会话已更新")
            else:
                self.accounts[job["phone"]] = {"phone": job["phone"], "status": "registered", "pin": job.get("pin", ""), "pin_set": bool(job.get("pin")), "kyc_status": "skipped" if job.get("skip_kyc") else "pending", "session_ready": True, "session": f"momo-fixture:{uuid.uuid4().hex}", "created_at": now, "updated_at": now}
                self._log(job_id, "MoMo 注册完成，已保存账号会话")
            job["status"] = "success"
            job["result"] = {"phone": job["phone"], "kyc_status": "skipped" if job.get("skip_kyc") else "pending", "session_ready": True}
            self._persist()

    def _poll_pool_code(self, phone: str) -> str:
        with self.lock:
            row = self.phones.get(phone) or {}
            endpoint = str(row.get("sms_url") or "").strip()
        if not endpoint:
            return ""
        try:
            with urllib.request.urlopen(endpoint, timeout=8) as response:
                body = response.read().decode("utf-8", "ignore")
            codes = re.findall(r"(?<!\d)(\d{4,8})(?!\d)", body)
            return codes[-1] if codes else ""
        except (OSError, urllib.error.URLError):
            return ""

    def submit_otp(self, job_id: str, code: str) -> dict[str, Any] | None:
        value = str(code or "").strip()
        if not re.fullmatch(r"\d{4,8}", value):
            return None
        with self.lock:
            job = self.jobs.get(str(job_id))
            if not job or job.get("status") != "waiting_otp":
                return None
            job.setdefault("_otp", []).append(value)
            job["status"] = "running"
            job["updated_at"] = _now()
            condition = self.conds.get(str(job_id))
            if condition:
                condition.notify_all()
            self._persist()
            return self._public_job(job)

    def start_payment(self, *, phone: str, qr_payload: str, amount: str = "", pin: str = "", proxy: str = "") -> dict[str, Any]:
        normalized = _normalize_phone(phone)
        payload = str(qr_payload or "").strip()
        if not normalized or not payload:
            raise ValueError("请选择已注册的 +84 账号并提供二维码内容")
        if not _valid_pin(str(pin or "").strip()):
            raise ValueError("支付密码请输入 4 到 8 位数字")
        with self.lock:
            account = self.accounts.get(normalized)
            if not account:
                raise ValueError("账号不存在，请先完成 MoMo 注册")
            job_id, _ = self._new_job(kind="payment", phone=normalized, pin=str(pin or ""), proxy=proxy, qr_payload=payload, amount=str(amount or ""), currency="VND", qr_hash=hashlib.sha256(payload.encode()).hexdigest()[:16])
        threading.Thread(target=self._run_payment, args=(job_id,), daemon=True, name=f"momo-payment-{job_id}").start()
        return self.get_job(job_id) or {}

    def _run_payment(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job["stage"] = "login"
            self._log(job_id, "正在使用已注册 MoMo 账号登录")
            account = self.accounts.get(job["phone"])
        if not self._provider_step("login", job_id, {"phone": job["phone"], "pin": job.get("pin", "") }):
            return
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            account = self.accounts.get(job["phone"])
            if account:
                account["session_ready"] = True
                account["last_login_at"] = _now()
                account["updated_at"] = _now()
            job["stage"] = "qr_scan"
            self._log(job_id, "账号登录成功，已识别二维码，准备提交扫码支付")
        if not self._provider_step("payment", job_id, {"phone": job["phone"], "qr_payload": job["qr_payload"], "amount": job.get("amount", "") }):
            return
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job["stage"] = "completed"
            job["status"] = "success"
            job["payment_id"] = f"momo-fixture-{uuid.uuid4().hex[:10]}"
            self._log(job_id, "扫码支付流程已完成")

    def _provider_step(self, operation: str, job_id: str, payload: dict[str, Any]) -> bool:
        with self.lock:
            mock = bool(self.settings.get("mock_mode", True))
            base_url = str(self.settings.get("api_base_url") or "").strip().rstrip("/")
        try:
            worker = MomoTaskWorker(build_provider(mock_mode=mock, base_url=base_url), log=lambda message: self._append_worker_log(job_id, message))
            return worker.run(operation, payload)
        except (OSError, urllib.error.URLError, RuntimeError, ValueError) as exc:
            with self.lock:
                job = self.jobs.get(job_id)
                if job:
                    job["status"] = "failed"
                    self._log(job_id, f"MoMo {operation} provider failed: {exc}")
            return False

    def _append_worker_log(self, job_id: str, message: str) -> None:
        with self.lock:
            if job_id in self.jobs:
                self._log(job_id, message)

    def relogin(self, phone: str) -> dict[str, Any]:
        return self.start_register(phone=phone, source="account", login_existing=True)

    def delete_account(self, phone: str) -> bool:
        normalized = _normalize_phone(phone)
        with self.lock:
            existed = normalized in self.accounts
            self.accounts.pop(normalized, None)
            if existed:
                self._persist()
            return existed
