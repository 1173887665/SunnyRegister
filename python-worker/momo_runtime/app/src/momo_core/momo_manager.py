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
from urllib.parse import urlsplit

from .momo_models import ProviderResult
from .momo_protocol import build_provider
from .momo_sms_provider import SmsLease, build_sms_provider, mask_secret
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


def _normalize_proxy(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    parsed = re.match(r"^(https?|socks5h?|socks4)://([^\s]+)$", text, re.IGNORECASE)
    if not parsed:
        raise ValueError("代理格式无效，请使用 http://、https:// 或 socks5://")
    if "@" in parsed.group(2):
        host_part = parsed.group(2).rsplit("@", 1)[-1]
    else:
        host_part = parsed.group(2)
    if ":" not in host_part or host_part.endswith(":"):
        raise ValueError("代理必须包含 host:port")
    return text


def _normalize_sms_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("短信读取 URL 不能为空")
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("短信读取 URL 必须是 http:// 或 https:// 地址")
    return text


def _normalize_endpoint(value: str, field: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} 必须是 http:// 或 https:// 地址")
    return text


def _split_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace("\r", "").split("\n")
    return [str(item).strip() for item in raw if str(item).strip()]


SMS_SOURCES = {"pool", "smsbower", "smspool", "grizzlysms", "hero_sms"}


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


_SENSITIVE_PUBLIC_KEYS = {
    "pin", "session", "token", "access_token", "refresh_token", "id_token",
    "proxy", "api_key", "authorization", "cookie", "cookies", "secret",
}


def _redact_public(value: Any, key: str = "") -> Any:
    """Remove credentials from objects returned by the local HTTP API.

    Provider adapters are allowed to return arbitrary metadata.  Persisting
    that metadata is useful for retries, but exposing it to the browser would
    leak account credentials or proxy authentication details.  Redaction is
    recursive so nested provider ``result`` objects are covered as well.
    """
    if key.lower() in _SENSITIVE_PUBLIC_KEYS or any(token in key.lower() for token in ("token", "authorization", "cookie", "secret")):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(item_key): _redact_public(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_public(item, key) for item in value]
    return value


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
        self._proxy_cursor = 0
        configured_api_base = os.getenv("OPAI_MOMO_API_BASE_URL", "").strip()
        # A temporary state file is how unit tests opt into the deterministic
        # local provider.  A long-lived production state file must never turn
        # an old persisted ``mock_mode`` flag back on after an upgrade.  An
        # explicit environment flag remains available for integration tests.
        explicit_test_store = state_file is not None and not configured_api_base and "OPAI_MOMO_MOCK_MODE" not in os.environ
        self._mock_mode_explicit = "OPAI_MOMO_MOCK_MODE" in os.environ or explicit_test_store
        configured_mock = True if explicit_test_store else _as_bool(os.getenv("OPAI_MOMO_MOCK_MODE", "0"), False)
        self.settings: dict[str, Any] = {
            "api_base_url": configured_api_base,
            # Live adapter is the runtime default. Unit tests can explicitly
            # set OPAI_MOMO_MOCK_MODE=1 without exposing a demo switch in UI.
            "mock_mode": configured_mock,
            "skip_kyc_default": True,
            "phone_source": os.getenv("OPAI_MOMO_PHONE_SOURCE", "pool").strip().lower() or "pool",
            "phone_country_code": os.getenv("OPAI_MOMO_PHONE_COUNTRY", "84").strip() or "84",
            "phone_prefix": os.getenv("OPAI_MOMO_PHONE_PREFIX", "+84").strip() or "+84",
            "sms_country_code": os.getenv("OPAI_MOMO_SMS_COUNTRY", "84").strip() or "84",
            "sms_service_code": os.getenv("OPAI_MOMO_SMS_SERVICE", "momo").strip() or "momo",
            "sms_api_base_url": os.getenv("OPAI_MOMO_SMS_API_BASE_URL", "").strip().rstrip("/"),
            "sms_api_key": os.getenv("OPAI_MOMO_SMS_API_KEY", "").strip(),
            "sms_max_price": os.getenv("OPAI_MOMO_SMS_MAX_PRICE", "").strip(),
            "sms_pool": os.getenv("OPAI_MOMO_SMS_POOL", "").strip(),
            "otp_timeout_sec": 300,
            "otp_poll_interval_sec": 3,
            "otp_max_resends": 2,
            "api_timeout_sec": 60,
            "api_headers_json": os.getenv("OPAI_MOMO_API_HEADERS", "").strip(),
            "proxy_pool": [],
            "proxy_mode": "round_robin",
            "proxy_required": False,
        }
        try:
            self.settings["proxy_pool"] = [_normalize_proxy(item) for item in _split_lines(os.getenv("OPAI_MOMO_PROXY_POOL", ""))]
        except ValueError:
            self.settings["proxy_pool"] = []
        self._load()
        forced_live_migration = False
        if not self._mock_mode_explicit:
            forced_live_migration = _as_bool(self.settings.get("mock_mode"), False)
            self.settings["mock_mode"] = False
        self._normalize_settings()
        if forced_live_migration:
            self._persist()
        self._recover_jobs()

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

    def _normalize_settings(self) -> None:
        """Repair old or hand-edited config values before any task uses them."""
        source = str(self.settings.get("phone_source") or "pool").strip().lower()
        self.settings["phone_source"] = source if source in SMS_SOURCES else "pool"
        for key, default in (("phone_country_code", "84"), ("sms_country_code", "84"), ("sms_service_code", "momo")):
            value = str(self.settings.get(key) or default).strip()
            self.settings[key] = value
        prefix = str(self.settings.get("phone_prefix") or "+84").strip()
        self.settings["phone_prefix"] = prefix if prefix.startswith("+") else f"+{prefix}"
        try:
            timeout = int(self.settings.get("otp_timeout_sec") or 300)
        except (TypeError, ValueError):
            timeout = 300
        self.settings["otp_timeout_sec"] = min(900, max(30, timeout))
        try:
            poll_interval = int(self.settings.get("otp_poll_interval_sec") or 3)
        except (TypeError, ValueError):
            poll_interval = 3
        self.settings["otp_poll_interval_sec"] = min(30, max(1, poll_interval))
        try:
            max_resends = int(self.settings.get("otp_max_resends") or 2)
        except (TypeError, ValueError):
            max_resends = 2
        self.settings["otp_max_resends"] = min(5, max(0, max_resends))
        try:
            api_timeout = int(self.settings.get("api_timeout_sec") or 60)
        except (TypeError, ValueError):
            api_timeout = 60
        self.settings["api_timeout_sec"] = min(300, max(5, api_timeout))
        self.settings["mock_mode"] = _as_bool(self.settings.get("mock_mode"), False)
        self.settings["skip_kyc_default"] = _as_bool(self.settings.get("skip_kyc_default"), True)
        try:
            self.settings["api_base_url"] = _normalize_endpoint(self.settings.get("api_base_url", ""), "MoMo API Base URL")
        except ValueError:
            self.settings["api_base_url"] = ""
        try:
            self.settings["sms_api_base_url"] = _normalize_endpoint(self.settings.get("sms_api_base_url", ""), "SMS API Base URL")
        except ValueError:
            self.settings["sms_api_base_url"] = ""
        self.settings["api_headers_json"] = str(self.settings.get("api_headers_json") or "").strip()
        pool: list[str] = []
        for item in _split_lines(self.settings.get("proxy_pool")):
            try:
                proxy = _normalize_proxy(item)
            except ValueError:
                continue
            if proxy not in pool:
                pool.append(proxy)
        self.settings["proxy_pool"] = pool[:100]
        self.settings["proxy_mode"] = "round_robin" if str(self.settings.get("proxy_mode") or "round_robin") not in {"round_robin", "random"} else str(self.settings.get("proxy_mode"))
        self.settings["proxy_required"] = _as_bool(self.settings.get("proxy_required"), False)
        try:
            rows = json.loads(self.pool_file.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                self.phones = {str(row.get("phone")): dict(row) for row in rows if isinstance(row, dict) and row.get("phone")}
        except (OSError, ValueError):
            pass

    def _recover_jobs(self) -> None:
        """Reconcile jobs left in flight when the worker process restarted.

        A thread condition and an external provider session cannot survive a
        process restart. Leaving such jobs in ``running`` would make the UI
        wait forever and would keep local phone leases reserved. Mark the job
        terminal, return local numbers to the pool, and cancel any external
        SMS activation on a best-effort basis.
        """
        stale_ids: list[str] = []
        with self.lock:
            for job_id, job in self.jobs.items():
                if str(job.get("status") or "") in {"success", "failed", "cancelled"}:
                    continue
                job["status"] = "failed"
                job["stage"] = "recovered"
                job["message"] = "任务在 Worker 重启后终止，请重新创建"
                job["updated_at"] = _now()
                job.setdefault("logs", []).append({"at": _now(), "message": job["message"]})
                stale_ids.append(str(job_id))
            if stale_ids:
                self._persist()
        for job_id in stale_ids:
            self._release_lease(job_id, success=False)
            self._release_phone(job_id, success=False)

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
        return _redact_public(clean)

    def _public_account(self, account: dict[str, Any]) -> dict[str, Any]:
        clean = dict(account)
        clean.pop("pin", None)
        clean.pop("session", None)
        clean.pop("proxy", None)
        return _redact_public(clean)

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
            result = {
                "api_base_url": self.settings.get("api_base_url", ""),
                "mock_mode": _as_bool(self.settings.get("mock_mode"), False),
                "live_mode": not _as_bool(self.settings.get("mock_mode"), False),
                "live_adapter_ready": bool(self.settings.get("api_base_url")) or _as_bool(self.settings.get("mock_mode"), False),
                "skip_kyc_default": _as_bool(self.settings.get("skip_kyc_default"), True),
                "phone_source": self.settings.get("phone_source", "pool"),
                "phone_country_code": self.settings.get("phone_country_code", "84"),
                "phone_prefix": self.settings.get("phone_prefix", "+84"),
                "sms_country_code": self.settings.get("sms_country_code", "84"),
                "sms_service_code": self.settings.get("sms_service_code", "momo"),
                "sms_api_base_url": self.settings.get("sms_api_base_url", ""),
                "sms_api_key_configured": bool(self.settings.get("sms_api_key")),
                "sms_api_key": mask_secret(str(self.settings.get("sms_api_key") or "")),
                "sms_max_price": self.settings.get("sms_max_price", ""),
                "sms_pool": self.settings.get("sms_pool", ""),
                "otp_timeout_sec": int(self.settings.get("otp_timeout_sec", 300)),
                "otp_poll_interval_sec": int(self.settings.get("otp_poll_interval_sec", 3)),
                "otp_max_resends": int(self.settings.get("otp_max_resends", 2)),
                "api_timeout_sec": int(self.settings.get("api_timeout_sec", 60)),
                "api_headers_configured": bool(self.settings.get("api_headers_json")),
                "proxy_pool": [_mask_proxy(item) for item in self.settings.get("proxy_pool", [])],
                "proxy_count": len(self.settings.get("proxy_pool", [])),
                "proxy_mode": self.settings.get("proxy_mode", "round_robin"),
                "proxy_required": bool(self.settings.get("proxy_required", False)),
                "phone_pool_count": len(self.phones),
                "phone_pool_available_count": sum(1 for row in self.phones.values() if str(row.get("status") or "available") == "available"),
                "phone_pool_reserved_count": sum(1 for row in self.phones.values() if str(row.get("status") or "available") == "reserved"),
            }
            return result

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if "api_base_url" in values:
                self.settings["api_base_url"] = _normalize_endpoint(values.get("api_base_url", ""), "MoMo API Base URL")
            if "mock_mode" in values:
                requested_mock = _as_bool(values.get("mock_mode"), False)
                if requested_mock and not self._mock_mode_explicit:
                    raise ValueError("生产运行时不允许启用本地 MoMo provider")
                self.settings["mock_mode"] = requested_mock
            if "skip_kyc_default" in values:
                self.settings["skip_kyc_default"] = _as_bool(values.get("skip_kyc_default"), True)
            source = str(values.get("phone_source", self.settings.get("phone_source", "pool")) or "pool").strip().lower()
            if source not in SMS_SOURCES:
                raise ValueError("号码来源无效")
            self.settings["phone_source"] = source
            for key, default in (("phone_country_code", "84"), ("sms_country_code", "84")):
                value = str(values.get(key, self.settings.get(key, default)) or default).strip().lstrip("+")
                if not re.fullmatch(r"\d{1,4}", value):
                    raise ValueError(f"{key} 必须是 1 到 4 位数字")
                self.settings[key] = value
            prefix = str(values.get("phone_prefix", self.settings.get("phone_prefix", "+84")) or "+84").strip()
            if not re.fullmatch(r"\+\d{1,4}", prefix if prefix.startswith("+") else f"+{prefix}"):
                raise ValueError("手机号前缀格式无效")
            self.settings["phone_prefix"] = prefix if prefix.startswith("+") else f"+{prefix}"
            self.settings["sms_service_code"] = str(values.get("sms_service_code", self.settings.get("sms_service_code", "momo")) or "momo").strip()
            self.settings["sms_api_base_url"] = _normalize_endpoint(values.get("sms_api_base_url", self.settings.get("sms_api_base_url", "")), "SMS API Base URL")
            if "sms_api_key" in values and str(values.get("sms_api_key") or "").strip():
                candidate_key = str(values.get("sms_api_key")).strip()
                if "***" not in candidate_key and "..." not in candidate_key:
                    self.settings["sms_api_key"] = candidate_key
            if bool(values.get("clear_sms_api_key")):
                self.settings["sms_api_key"] = ""
            self.settings["sms_max_price"] = str(values.get("sms_max_price", self.settings.get("sms_max_price", "")) or "").strip()
            self.settings["sms_pool"] = str(values.get("sms_pool", self.settings.get("sms_pool", "")) or "").strip()
            try:
                timeout = int(values.get("otp_timeout_sec", self.settings.get("otp_timeout_sec", 300)) or 300)
            except (TypeError, ValueError) as exc:
                raise ValueError("OTP 超时必须是数字") from exc
            self.settings["otp_timeout_sec"] = min(900, max(30, timeout))
            try:
                poll_interval = int(values.get("otp_poll_interval_sec", self.settings.get("otp_poll_interval_sec", 3)) or 3)
            except (TypeError, ValueError) as exc:
                raise ValueError("OTP 轮询间隔必须是数字") from exc
            self.settings["otp_poll_interval_sec"] = min(30, max(1, poll_interval))
            try:
                max_resends = int(values.get("otp_max_resends", self.settings.get("otp_max_resends", 2)) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("OTP 重发次数必须是数字") from exc
            self.settings["otp_max_resends"] = min(5, max(0, max_resends))
            try:
                api_timeout = int(values.get("api_timeout_sec", self.settings.get("api_timeout_sec", 60)) or 60)
            except (TypeError, ValueError) as exc:
                raise ValueError("MoMo API 超时必须是数字") from exc
            self.settings["api_timeout_sec"] = min(300, max(5, api_timeout))
            if "api_headers_json" in values:
                raw_headers = str(values.get("api_headers_json") or "").strip()
                if raw_headers:
                    try:
                        parsed_headers = json.loads(raw_headers)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("MoMo API Headers 必须是 JSON 对象") from exc
                    if not isinstance(parsed_headers, dict):
                        raise ValueError("MoMo API Headers 必须是 JSON 对象")
                self.settings["api_headers_json"] = raw_headers
            if "proxy_pool" in values:
                incoming_proxy_lines = _split_lines(values.get("proxy_pool"))
                # GET /settings intentionally masks proxy credentials.  If a
                # client sends that masked snapshot back unchanged, preserve
                # the real pool rather than storing the mask as a credential.
                preserve_proxy_pool = bool(incoming_proxy_lines and all("***" in item for item in incoming_proxy_lines) and self.settings.get("proxy_pool"))
                if not preserve_proxy_pool:
                    proxies: list[str] = []
                    for item in incoming_proxy_lines:
                        proxy = _normalize_proxy(item)
                        if proxy not in proxies:
                            proxies.append(proxy)
                    if len(proxies) > 100:
                        raise ValueError("代理池最多保存 100 条")
                    self.settings["proxy_pool"] = proxies
            proxy_mode = str(values.get("proxy_mode", self.settings.get("proxy_mode", "round_robin")) or "round_robin")
            if proxy_mode not in {"round_robin", "random"}:
                raise ValueError("代理轮换方式无效")
            self.settings["proxy_mode"] = proxy_mode
            self.settings["proxy_required"] = _as_bool(values.get("proxy_required", self.settings.get("proxy_required", False)), False)
            self._normalize_settings()
            self._persist()
            return self.get_settings()

    def check_proxy_pool(self, values: Any = None) -> dict[str, Any]:
        """Validate configured proxy syntax without consuming a task slot."""
        raw = _split_lines(self.settings.get("proxy_pool") if values is None else values)
        results: list[dict[str, Any]] = []
        for index, item in enumerate(raw, 1):
            try:
                proxy = _normalize_proxy(item)
                results.append({"line": index, "ok": True, "proxy": _mask_proxy(proxy), "error": ""})
            except ValueError as exc:
                results.append({"line": index, "ok": False, "proxy": _mask_proxy(item), "error": str(exc)})
        return {"total": len(results), "available": sum(1 for item in results if item["ok"]), "unavailable": sum(1 for item in results if not item["ok"]), "results": results}

    def check_settings(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return actionable configuration diagnostics without purchasing a number."""
        with self.lock:
            snapshot = dict(self.settings)
            if values:
                snapshot.update({key: value for key, value in values.items() if key not in {"sms_api_key"} or str(value or "").strip()})
            if not self._mock_mode_explicit:
                snapshot["mock_mode"] = False
            source = str(snapshot.get("phone_source") or "pool").strip().lower()
            proxy_pool = snapshot.get("proxy_pool") or []
            phone_count = len(self.phones)
        checks: list[dict[str, Any]] = []
        live = not _as_bool(snapshot.get("mock_mode"), False)
        checks.append({"name": "momo_adapter", "ok": bool(snapshot.get("api_base_url")) if live else True, "message": "MoMo API Base URL 已配置" if snapshot.get("api_base_url") else ("本地演练模式" if not live else "缺少 MoMo API Base URL")})
        checks.append({"name": "phone_source", "ok": source in SMS_SOURCES, "message": source if source in SMS_SOURCES else "号码来源无效"})
        if source == "pool":
            checks.append({"name": "phone_pool", "ok": phone_count > 0, "message": f"号码池 {phone_count} 条" if phone_count else "号码池为空，请导入 +84 号码"})
        else:
            checks.append({"name": "sms_api_key", "ok": bool(str(snapshot.get("sms_api_key") or "").strip()), "message": "短信平台 API Key 已配置" if snapshot.get("sms_api_key") else "短信平台 API Key 未配置"})
        proxy_check = self.check_proxy_pool(proxy_pool)
        checks.append({"name": "proxy_pool", "ok": proxy_check["unavailable"] == 0 and (not _as_bool(snapshot.get("proxy_required"), False) or proxy_check["available"] > 0), "message": f"可用 {proxy_check['available']} / {proxy_check['total']}"})
        return {"ok": all(item["ok"] for item in checks), "live_mode": live, "checks": checks, "proxy": proxy_check}

    def import_phones(self, raw: str) -> dict[str, int]:
        inserted = 0
        skipped = 0
        with self.lock:
            for line in str(raw or "").splitlines():
                if "----" not in line:
                    skipped += 1
                    continue
                phone_raw, sms_url = [item.strip() for item in line.split("----", 1)]
                phone = _normalize_phone(phone_raw)
                try:
                    sms_url = _normalize_sms_url(sms_url)
                except ValueError:
                    skipped += 1
                    continue
                if not phone or phone in self.phones:
                    skipped += 1
                    continue
                self.phones[phone] = {"phone": phone, "sms_url": sms_url, "status": "available", "country": "VN", "created_at": _now()}
                inserted += 1
            self._persist()
            return {"inserted": inserted, "skipped": skipped, "total": len(self.phones)}

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

    def _select_proxy(self, requested: str = "") -> str:
        """Return the next configured proxy; task-level values never bypass the pool."""
        with self.lock:
            pool = list(self.settings.get("proxy_pool") or [])
            if pool:
                if self.settings.get("proxy_mode") == "random":
                    import random
                    return str(random.choice(pool))
                proxy = str(pool[self._proxy_cursor % len(pool)])
                self._proxy_cursor += 1
                return proxy
            if bool(self.settings.get("proxy_required")):
                raise ValueError("系统配置要求代理，但代理池为空")
            return _normalize_proxy(requested) if requested else ""

    def _sms_settings_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.settings)

    def _acquire_sms_lease(self, source: str, proxy: str = "") -> SmsLease:
        settings = self._sms_settings_snapshot()
        settings["phone_source"] = source
        settings["sms_proxy"] = proxy
        api_key = str(settings.get("sms_api_key") or "").strip()
        if not api_key:
            raise ValueError(f"{source} API Key 未配置，请先到 MoMo 系统配置保存")
        provider = build_sms_provider(settings)
        lease = provider.acquire()
        if not lease.phone or not lease.activation_id:
            raise RuntimeError(f"{source} 未返回有效手机号或激活 ID")
        return lease

    @staticmethod
    def _close_lease(lease: SmsLease, snapshot: dict[str, Any], *, success: bool) -> None:
        config = dict(snapshot)
        config["phone_source"] = lease.provider
        config["sms_api_key"] = lease.api_key or config.get("sms_api_key", "")
        provider = build_sms_provider(config)
        (provider.finish if success else provider.cancel)(lease)

    def _release_lease(self, job_id: str, *, success: bool) -> None:
        """Close an external SMS activation exactly once."""
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.get("_sms_released"):
                return
            source = str(job.get("sms_provider") or "")
            activation_id = str(job.get("sms_activation_id") or "")
            phone = str(job.get("phone") or "")
            api_key = str(job.get("_sms_api_key") or "")
            snapshot = dict(job.get("_sms_config") if isinstance(job.get("_sms_config"), dict) else self.settings)
            job["_sms_released"] = True
        if not source or source == "pool" or not activation_id:
            return
        snapshot["phone_source"] = source
        snapshot["sms_api_key"] = api_key or snapshot.get("sms_api_key", "")
        try:
            lease = SmsLease(source, phone, activation_id, api_key)
            self._close_lease(lease, snapshot, success=success)
            self._log(job_id, "短信激活已完成" if success else "短信激活已取消")
        except Exception as exc:
            self._log(job_id, f"短信激活释放失败: {exc}")

    def _release_phone(self, job_id: str, *, success: bool) -> None:
        """Return a local-pool number on failure, or mark it consumed on success."""
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or not job.get("phone_pool_reserved"):
                return
            phone = str(job.get("phone") or "")
            row = self.phones.get(phone)
            if row:
                row["status"] = "used" if success else "available"
                row["updated_at"] = _now()
            job["phone_pool_reserved"] = False
            self._persist()

    def _finish_job(self, job_id: str, *, success: bool, message: str) -> None:
        """Set terminal state and release every resource owned by the job."""
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            if str(job.get("status") or "") == "cancelled":
                return
            job["status"] = "success" if success else "failed"
            self._log(job_id, message)
        self._release_lease(job_id, success=success)
        self._release_phone(job_id, success=success)

    @staticmethod
    def _provider_state(result: ProviderResult) -> str:
        """Normalize adapter progress hints into manager states."""
        data = result.data if isinstance(result.data, dict) else {}
        raw = str(data.get("status") or data.get("state") or data.get("next_step") or "").strip().lower().replace("-", "_")
        if _as_bool(data.get("requires_otp"), False) or _as_bool(data.get("otp_required"), False):
            return "waiting_otp"
        if _as_bool(data.get("requires_confirmation"), False) or _as_bool(data.get("confirmation_required"), False):
            return "awaiting_confirmation"
        if raw in {"waiting_otp", "awaiting_otp", "otp_required", "requires_otp", "pending_otp"}:
            return "waiting_otp"
        if raw in {"awaiting_confirmation", "confirmation_required", "requires_confirmation", "pending_confirmation", "pending"}:
            return "awaiting_confirmation"
        if raw in {"success", "succeeded", "completed", "complete", "paid", "ok"}:
            return "success"
        return "success"

    def _wait_for_manual_otp(self, job_id: str) -> str:
        """Wait for an OTP submitted through the HTTP API or UI."""
        while True:
            with self.lock:
                job = self.jobs.get(job_id)
                if not job or str(job.get("status") or "") == "cancelled":
                    return ""
                queued = job.get("_otp") or []
                if queued:
                    return str(queued.pop(0))
                remaining = float(job.get("_otp_deadline") or 0) - time.time()
                condition = self.conds.get(job_id)
                if remaining <= 0:
                    return ""
                if condition:
                    condition.wait(timeout=min(2, remaining))
                else:
                    time.sleep(min(2, remaining))

    def _mark_waiting_otp(self, job_id: str, *, stage: str, context: dict[str, Any], message: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or str(job.get("status") or "") == "cancelled":
                return
            timeout = int(job.get("otp_timeout_sec") or self.settings.get("otp_timeout_sec", 300))
            job["status"] = "waiting_otp"
            job["stage"] = stage
            job["_otp_deadline"] = time.time() + timeout
            job["_payment_context"] = dict(context)
            self._log(job_id, message)

    def _mark_awaiting_confirmation(self, job_id: str, *, context: dict[str, Any], message: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or str(job.get("status") or "") == "cancelled":
                return
            job["status"] = "awaiting_confirmation"
            job["stage"] = "confirm"
            job["_payment_context"] = dict(context)
            self._log(job_id, message)

    def _complete_payment_result(self, job_id: str, session: dict[str, Any], result: ProviderResult, *, depth: int = 0) -> None:
        """Handle adapter responses, including payment OTP/confirmation."""
        if depth > 3:
            self._finish_job(job_id, success=False, message="MoMo 支付验证步骤超过最大重试次数")
            return
        if not result.ok:
            self._finish_job(job_id, success=False, message=result.error or "MoMo 扫码支付失败")
            return
        data = dict(result.data or {})
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or str(job.get("status") or "") == "cancelled":
                return
            previous = dict(job.get("_payment_context") if isinstance(job.get("_payment_context"), dict) else {})
        context = {**previous, **data}
        state = self._provider_state(result)
        if state == "waiting_otp":
            self._mark_waiting_otp(job_id, stage="payment_otp", context=context, message="MoMo 支付需要 OTP，请输入收到的验证码")
            code = self._wait_for_manual_otp(job_id)
            if not code:
                self._finish_job(job_id, success=False, message="支付 OTP 等待超时或任务已取消")
                return
            with self.lock:
                job = self.jobs.get(job_id)
                if not job or str(job.get("status") or "") == "cancelled":
                    return
                job["status"] = "running"
                job["stage"] = "payment_verify_otp"
                self._log(job_id, f"支付 OTP 已提交（{len(code)} 位）")
                payload_session = dict(session)
                payload_session.update(context)
            verified = self._provider_step_result("payment_verify_otp", job_id, {"session": payload_session, "otp": code, "payment": context, "phone": payload_session.get("phone", "")})
            self._complete_payment_result(job_id, payload_session, verified, depth=depth + 1)
            return
        if state == "awaiting_confirmation":
            self._mark_awaiting_confirmation(job_id, context=context, message="二维码已提交，等待支付确认")
            return
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or str(job.get("status") or "") == "cancelled":
                return
            job["stage"] = "completed"
            job["status"] = "success"
            job["payment_id"] = str(data.get("payment_id") or data.get("id") or f"momo-payment-{uuid.uuid4().hex[:10]}")
            job["result"] = {key: value for key, value in data.items() if key not in {"token", "session", "pin", "proxy"}}
            self._log(job_id, "扫码支付流程已完成")
            self._persist()

    def _run_payment_confirm_safe(self, job_id: str) -> None:
        try:
            with self.lock:
                job = self.jobs.get(job_id)
                if not job or str(job.get("status") or "") != "awaiting_confirmation":
                    return
                session = dict(job.get("_session") if isinstance(job.get("_session"), dict) else {})
                context = dict(job.get("_payment_context") if isinstance(job.get("_payment_context"), dict) else {})
                job["status"] = "running"
                job["stage"] = "confirm"
                self._log(job_id, "正在提交 MoMo 支付确认")
            payload_session = {**session, **context}
            result = self._provider_step_result("payment_confirm", job_id, {"session": payload_session, "payment": context, "phone": payload_session.get("phone", "")})
            self._complete_payment_result(job_id, payload_session, result)
        except Exception as exc:
            self._finish_job(job_id, success=False, message=f"MoMo 支付确认异常: {exc}")

    def confirm_payment(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(str(job_id))
            if not job or str(job.get("kind") or "") != "payment" or str(job.get("status") or "") != "awaiting_confirmation":
                return None
        threading.Thread(target=self._run_payment_confirm_safe, args=(str(job_id),), daemon=True, name=f"momo-payment-confirm-{job_id}").start()
        return self.get_job(str(job_id))

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(str(job_id))
            if not job or str(job.get("status") or "") in {"success", "failed", "cancelled"}:
                return None
            job["status"] = "cancelled"
            job["stage"] = "cancelled"
            self._log(str(job_id), "任务已取消")
            condition = self.conds.get(str(job_id))
            if condition:
                condition.notify_all()
        self._release_lease(str(job_id), success=False)
        self._release_phone(str(job_id), success=False)
        return self.get_job(str(job_id))

    def _allocate_phone(self, requested: str) -> str:
        normalized = _normalize_phone(requested)
        with self.lock:
            if normalized:
                row = self.phones.get(normalized)
                if not row or str(row.get("status") or "available") != "available":
                    raise ValueError("指定号码不在可用的 MoMo 号码池中")
                row["status"] = "reserved"
                row["updated_at"] = _now()
                self._persist()
                return normalized
            for phone, row in self.phones.items():
                if str(row.get("status") or "available") == "available":
                    row["status"] = "reserved"
                    row["updated_at"] = _now()
                    self._persist()
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

    def start_register(self, *, phone: str = "", source: str = "", pin: str = "", login_existing: bool = False, skip_kyc: bool | None = None, proxy: str = "", profile: dict[str, Any] | None = None) -> dict[str, Any]:
        pin = str(pin or "").strip()
        if not _valid_pin(pin):
            raise ValueError("支付密码请输入 4 到 8 位数字")
        profile_data = {
            "display_name": str((profile or {}).get("display_name") or "").strip()[:80],
            "email": str((profile or {}).get("email") or "").strip()[:160],
            "date_of_birth": str((profile or {}).get("date_of_birth") or "").strip()[:32],
            "address": str((profile or {}).get("address") or "").strip()[:240],
        }
        if profile_data["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", profile_data["email"]):
            raise ValueError("邮箱格式无效")
        with self.lock:
            configured_source = str(self.settings.get("phone_source") or "pool").strip().lower()
            otp_timeout = int(self.settings.get("otp_timeout_sec", 300))
        selected_proxy = ""
        # Registration always follows the persisted system source. The request
        # field is accepted for backwards compatibility but cannot silently
        # bypass the configured SMS/number policy.
        effective_source = configured_source
        if login_existing:
            effective_source = "account"
        elif effective_source not in SMS_SOURCES:
            raise ValueError("号码来源无效")
        lease: SmsLease | None = None
        requested_phone = str(phone or "").strip()
        phone_pool_reserved = False
        if login_existing:
            normalized = _normalize_phone(requested_phone)
            if not normalized:
                raise ValueError("登录已有账号必须提供 +84 手机号")
        elif requested_phone:
            normalized = _normalize_phone(requested_phone)
            if not normalized:
                raise ValueError("手机号格式无效，请使用 +84 越南号码")
            if effective_source != "pool":
                raise ValueError("当前号码来源由系统配置自动取号，注册任务请留空手机号")
            normalized = self._allocate_phone(normalized)
            phone_pool_reserved = True
        elif effective_source == "pool":
            normalized = self._allocate_phone("")
            phone_pool_reserved = True
        else:
            selected_proxy = self._select_proxy(proxy)
            lease = self._acquire_sms_lease(effective_source, selected_proxy)
            normalized = _normalize_phone(lease.phone)
            if not normalized:
                try:
                    self._close_lease(lease, self._sms_settings_snapshot(), success=False)
                except Exception:
                    pass
                raise ValueError(f"{effective_source} 返回的手机号不是有效的 +84 越南号码")
        if not selected_proxy:
            selected_proxy = self._select_proxy(proxy)
        sms_config = self._sms_settings_snapshot()
        sms_config["sms_proxy"] = selected_proxy
        try:
            with self.lock:
                existing = self.accounts.get(normalized)
                if login_existing and not existing:
                    raise ValueError("该手机号尚未在 MoMo 账号库中")
                if not login_existing and existing:
                    raise ValueError("该手机号已经注册，请使用登录已有号")
                job_id, _ = self._new_job(
                    kind="register", phone=normalized, pin=pin, proxy=selected_proxy,
                    source=effective_source, login_existing=bool(login_existing),
                    skip_kyc=self.settings.get("skip_kyc_default", True) if skip_kyc is None else bool(skip_kyc),
                    country="VN", phone_country_code=self.settings.get("phone_country_code", "84"),
                    sms_provider=lease.provider if lease else effective_source,
                    sms_activation_id=lease.activation_id if lease else "",
                    _sms_api_key=lease.api_key if lease else "",
                    _sms_config=sms_config,
                    phone_pool_reserved=phone_pool_reserved,
                    otp_timeout_sec=otp_timeout,
                    profile=profile_data,
                    _session={"phone": normalized, "proxy": selected_proxy},
                )
        except Exception:
            if lease:
                try:
                    self._close_lease(lease, sms_config, success=False)
                except Exception:
                    pass
            if phone_pool_reserved:
                with self.lock:
                    row = self.phones.get(normalized)
                    if row:
                        row["status"] = "available"
                        self._persist()
            raise
        # Capture the creation response before the worker thread can advance
        # the job to ``waiting_otp``.  Callers rely on a stable ``running``
        # status for the create response; subsequent polling exposes the real
        # state transition.
        created = self.get_job(job_id) or {}
        threading.Thread(target=self._run_register_safe, args=(job_id,), daemon=True, name=f"momo-register-{job_id}").start()
        return created

    def _run_register_safe(self, job_id: str) -> None:
        try:
            self._run_register(job_id)
        except Exception as exc:
            self._finish_job(job_id, success=False, message=f"MoMo 注册任务异常: {exc}")

    def _run_register(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            phone = str(job.get("phone") or "")
            proxy = str(job.get("proxy") or "")
            source = str(job.get("source") or "pool")
            session = dict(job.get("_session") if isinstance(job.get("_session"), dict) else {"phone": phone, "proxy": proxy})
            timeout = int(job.get("otp_timeout_sec") or self.settings.get("otp_timeout_sec", 300))
            self._log(job_id, "初始化 MoMo +84 设备会话")
            account = dict(self.accounts.get(phone) or {})
            login_existing = bool(job.get("login_existing"))
        if login_existing:
            result = self._provider_step_result("login", job_id, {"phone": phone, "pin": str(job.get("pin") or account.get("pin") or ""), "proxy": proxy, "session": session})
            if not result.ok:
                self._finish_job(job_id, success=False, message=result.error or "MoMo 登录失败")
                return
            session.update(result.data)
            with self.lock:
                job = self.jobs.get(job_id)
                account = self.accounts.get(phone)
                if job and account is not None:
                    now = _now()
                    account.update({"status": "registered", "session_ready": True, "last_login_at": now, "updated_at": now, "session": session.get("session") or session.get("token") or account.get("session", "")})
                    job["_session"] = session
                    job["stage"] = "completed"
                    job["result"] = {"phone": phone, "session_ready": True}
                    job["status"] = "success"
                    self._log(job_id, "MoMo 已登录，账号会话已更新")
                    self._persist()
            return
        started = self._provider_step_result("register_start", job_id, {"phone": phone, "proxy": proxy, "login_existing": bool(job.get("login_existing"))})
        if not started.ok:
            self._finish_job(job_id, success=False, message=started.error or "MoMo 注册初始化失败")
            return
        session.update(started.data)
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job["_session"] = session
            job["status"] = "waiting_otp"
            job["stage"] = "otp"
            job["_otp_deadline"] = time.time() + timeout
            self._log(job_id, "正在请求 MoMo 注册 OTP")
        sent = self._provider_step_result("register_send_otp", job_id, {"session": session, "phone": phone, "proxy": proxy})
        if not sent.ok:
            self._finish_job(job_id, success=False, message=sent.error or "MoMo OTP 发送失败")
            return
        session.update(sent.data)
        with self.lock:
            job = self.jobs.get(job_id)
            if job:
                job["_session"] = session
                self._persist()
        lease: SmsLease | None = None
        provider = None
        if source != "pool" and source in SMS_SOURCES:
            snapshot = self._sms_settings_snapshot()
            snapshot["phone_source"] = source
            snapshot["sms_api_key"] = str(job.get("_sms_api_key") or snapshot.get("sms_api_key") or "")
            snapshot["sms_proxy"] = proxy
            provider = build_sms_provider(snapshot)
            lease = SmsLease(source, phone, str(job.get("sms_activation_id") or ""), str(job.get("_sms_api_key") or ""))
        ignored: set[str] = set()
        code = ""
        poll_interval = int(self.settings.get("otp_poll_interval_sec", 3))
        max_resends = int(self.settings.get("otp_max_resends", 2))
        resends = 0
        while time.time() < float(job.get("_otp_deadline") or 0):
            with self.lock:
                job = self.jobs.get(job_id)
                if not job:
                    return
                queued = job.get("_otp") or []
                if queued:
                    code = str(queued.pop(0))
                    break
                deadline = float(job.get("_otp_deadline") or 0)
                condition = self.conds.get(job_id)
            if provider and lease:
                try:
                    code = str(provider.wait_code(lease, min(poll_interval, max(1, int(deadline - time.time()))), ignored) or "")
                except Exception as exc:
                    self._append_worker_log(job_id, f"短信平台轮询失败: {exc}")
                    code = ""
            elif source == "pool":
                # Give a manually submitted OTP a chance to wake the worker
                # before making a potentially slow URL request.  This keeps
                # the UI responsive even when a configured SMS URL is down.
                with self.lock:
                    condition = self.conds.get(job_id)
                    remaining = max(0.0, float(self.jobs.get(job_id, {}).get("_otp_deadline") or 0) - time.time())
                    if condition and remaining > 0:
                        condition.wait(timeout=min(0.25, remaining))
                    current = self.jobs.get(job_id)
                    queued = current.get("_otp") if current else []
                    if queued:
                        code = str(queued.pop(0))
                if not code:
                    code = self._poll_pool_code(phone, proxy, ignored)
            if code:
                break
            with self.lock:
                remaining = max(0, int(float(self.jobs.get(job_id, {}).get("_otp_deadline") or 0) - time.time()))
                if remaining <= 0:
                    break
                condition = self.conds.get(job_id)
                if condition:
                    condition.wait(timeout=min(poll_interval, remaining))
            if provider and lease and resends < max_resends and time.time() + poll_interval >= float(job.get("_otp_deadline") or 0):
                try:
                    if provider.resend(lease):
                        resends += 1
                        with self.lock:
                            job = self.jobs.get(job_id)
                            if job:
                                job["_otp_deadline"] = time.time() + timeout
                                self._log(job_id, f"OTP 已重发（{resends}/{max_resends}）")
                except Exception as exc:
                    self._append_worker_log(job_id, f"OTP 重发失败: {exc}")
        if not code:
            self._finish_job(job_id, success=False, message="OTP 等待超时")
            return
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job["status"] = "running"
            job["stage"] = "verify_otp"
            self._log(job_id, f"OTP 已提交（{len(code)} 位）")
        verified = self._provider_step_result("register_verify_otp", job_id, {"session": session, "otp": code, "phone": phone, "proxy": proxy})
        if not verified.ok:
            self._finish_job(job_id, success=False, message=verified.error or "MoMo OTP 校验失败")
            return
        session.update(verified.data)
        profile = {"phone": phone, "country": "VN", "skip_kyc": bool(job.get("skip_kyc", True)), **(job.get("profile") if isinstance(job.get("profile"), dict) else {})}
        profile_result = self._provider_step_result("register_profile", job_id, {"session": session, "profile": profile, "phone": phone, "skip_kyc": profile["skip_kyc"]})
        if not profile_result.ok:
            self._finish_job(job_id, success=False, message=profile_result.error or "MoMo 资料提交失败")
            return
        session.update(profile_result.data)
        pin_result = self._provider_step_result("register_pin", job_id, {"session": session, "phone": phone, "pin": str(job.get("pin") or ""), "proxy": proxy})
        if not pin_result.ok:
            self._finish_job(job_id, success=False, message=pin_result.error or "MoMo PIN 设置失败")
            return
        session.update(pin_result.data)
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            now = _now()
            if job.get("login_existing"):
                account = self.accounts.setdefault(phone, {})
                account.update({"phone": phone, "status": "registered", "session_ready": True, "last_login_at": now, "updated_at": now, "session": session.get("session") or session.get("token") or account.get("session", ""), "kyc_status": account.get("kyc_status", "skipped")})
                self._log(job_id, "MoMo 已登录，账号会话已更新")
            else:
                profile_data = job.get("profile") if isinstance(job.get("profile"), dict) else {}
                self.accounts[phone] = {"phone": phone, "status": "registered", "pin": job.get("pin", ""), "pin_set": bool(job.get("pin")), "kyc_status": "skipped" if job.get("skip_kyc") else "pending", "session_ready": True, "session": session.get("session") or session.get("token") or f"momo-session:{uuid.uuid4().hex}", "display_name": profile_data.get("display_name", ""), "email": profile_data.get("email", ""), "date_of_birth": profile_data.get("date_of_birth", ""), "address": profile_data.get("address", ""), "created_at": now, "updated_at": now}
                self._log(job_id, "MoMo 注册完成，已保存账号会话")
            job["_session"] = session
            job["stage"] = "completed"
            job["result"] = {"phone": phone, "kyc_status": "skipped" if job.get("skip_kyc") else "pending", "session_ready": True}
            job["status"] = "success"
            self._persist()
        self._release_lease(job_id, success=True)
        self._release_phone(job_id, success=True)

    def _poll_pool_code(self, phone: str, proxy: str = "", ignored: set[str] | None = None) -> str:
        with self.lock:
            row = self.phones.get(phone) or {}
            endpoint = str(row.get("sms_url") or "").strip()
        if not endpoint:
            return ""
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
            )
            with opener.open(endpoint, timeout=2) as response:
                body = response.read().decode("utf-8", "ignore")
            codes = re.findall(r"(?<!\d)(\d{4,8})(?!\d)", body)
            ignored_set = ignored or set()
            for code in reversed(codes):
                if hashlib.sha256(code.encode("utf-8")).hexdigest() not in ignored_set:
                    return code
            return ""
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
        if len(payload) > 16_384:
            raise ValueError("二维码内容过长")
        amount_text = str(amount or "").strip()
        if amount_text and (not re.fullmatch(r"\d{1,12}", amount_text) or int(amount_text) <= 0):
            raise ValueError("金额必须是大于 0 的 VND 整数")
        if not _valid_pin(str(pin or "").strip()):
            raise ValueError("支付密码请输入 4 到 8 位数字")
        with self.lock:
            account = self.accounts.get(normalized)
            if not account:
                raise ValueError("账号不存在，请先完成 MoMo 注册")
            effective_pin = str(pin or "").strip() or str(account.get("pin") or "")
            if not _valid_pin(effective_pin):
                raise ValueError("账号没有可用 PIN，请先在注册或登录时设置")
        selected_proxy = self._select_proxy(proxy)
        with self.lock:
            job_id, _ = self._new_job(kind="payment", phone=normalized, pin=effective_pin, proxy=selected_proxy, qr_payload=payload, amount=amount_text, currency="VND", qr_hash=hashlib.sha256(payload.encode()).hexdigest()[:16], _session={"phone": normalized, "proxy": selected_proxy, "session": account.get("session", "")})
        created = self.get_job(job_id) or {}
        threading.Thread(target=self._run_payment_safe, args=(job_id,), daemon=True, name=f"momo-payment-{job_id}").start()
        return created

    def _run_payment_safe(self, job_id: str) -> None:
        try:
            self._run_payment(job_id)
        except Exception as exc:
            self._finish_job(job_id, success=False, message=f"MoMo 支付任务异常: {exc}")

    def _run_payment(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job["stage"] = "login"
            self._log(job_id, "正在使用已注册 MoMo 账号登录")
            account = self.accounts.get(job["phone"])
            session = dict(job.get("_session") if isinstance(job.get("_session"), dict) else {})
        login_result = self._provider_step_result("login", job_id, {"phone": job["phone"], "pin": job.get("pin", ""), "proxy": job.get("proxy", ""), "session": session})
        if not login_result.ok:
            self._finish_job(job_id, success=False, message=login_result.error or "MoMo 登录失败")
            return
        session.update(login_result.data)
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            account = self.accounts.get(job["phone"])
            if account:
                account["session_ready"] = True
                account["last_login_at"] = _now()
                account["updated_at"] = _now()
                if login_result.data.get("session") or login_result.data.get("token"):
                    account["session"] = login_result.data.get("session") or login_result.data.get("token")
            job["_session"] = session
            job["stage"] = "qr_scan"
            self._log(job_id, "账号登录成功，已识别二维码，准备提交扫码支付")
        payment_result = self._provider_step_result("payment", job_id, {"phone": job["phone"], "qr_payload": job["qr_payload"], "amount": job.get("amount", ""), "currency": job.get("currency", "VND"), "proxy": job.get("proxy", ""), "session": session})
        with self.lock:
            job = self.jobs.get(job_id)
            if job:
                job["_session"] = session
                self._persist()
        self._complete_payment_result(job_id, session, payment_result)

    def _provider_step(self, operation: str, job_id: str, payload: dict[str, Any]) -> bool:
        return self._provider_step_result(operation, job_id, payload).ok

    def _provider_step_result(self, operation: str, job_id: str, payload: dict[str, Any]) -> ProviderResult:
        with self.lock:
            mock = _as_bool(self.settings.get("mock_mode"), False)
            base_url = str(self.settings.get("api_base_url") or "").strip().rstrip("/")
            timeout = int(self.settings.get("api_timeout_sec", 60))
            headers = str(self.settings.get("api_headers_json") or "")
        try:
            worker = MomoTaskWorker(build_provider(mock_mode=mock, base_url=base_url, timeout=timeout, headers=headers), log=lambda message: self._append_worker_log(job_id, message))
            return worker.run_result(operation, payload)
        except (OSError, urllib.error.URLError, RuntimeError, ValueError) as exc:
            with self.lock:
                job = self.jobs.get(job_id)
                if job:
                    job["status"] = "failed"
                    self._log(job_id, f"MoMo {operation} provider failed: {exc}")
            return ProviderResult(False, {}, str(exc))

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
