from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from .otp_candidates import extract_otp_candidates


LUBAN_DEFAULT_BASE_URL = "https://lubansms.com/v2/api/"


class LubanSMSError(RuntimeError):
    def __init__(self, message: str, *, code: Any = None, terminal: bool = False):
        self.code = code
        self.terminal = terminal
        super().__init__(message)


@dataclass(slots=True)
class LubanActivation:
    request_id: str
    number: str


class LubanSMSClient:
    def __init__(self, config: dict[str, Any], proxies: dict[str, str] | None = None):
        self.base_url = str(config.get("luban_base_url") or LUBAN_DEFAULT_BASE_URL).strip().rstrip("/")
        self.api_key = str(config.get("luban_api_key") or "").strip()
        self.service_id = str(config.get("luban_service_id") or "").strip()
        self.proxies = proxies or None
        if not self.api_key:
            raise LubanSMSError("LubanSMS API Key 未配置", terminal=True)
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", self.service_id):
            raise LubanSMSError("LubanSMS 供应商编号无效", terminal=True)

    def _request(self, endpoint: str, **params: Any) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/{endpoint}",
            params={"apikey": self.api_key, **params},
            headers={"Accept": "application/json"},
            timeout=20,
            proxies=self.proxies,
        )
        if response.status_code >= 400:
            raise LubanSMSError(f"LubanSMS HTTP {response.status_code}", terminal=response.status_code < 500)
        try:
            payload = response.json()
        except Exception as exc:
            raise LubanSMSError("LubanSMS 返回非 JSON 数据") from exc
        if not isinstance(payload, dict):
            raise LubanSMSError("LubanSMS 返回格式无效")
        return payload

    @staticmethod
    def _error(payload: dict[str, Any], fallback: str) -> LubanSMSError:
        code = payload.get("code")
        message = str(payload.get("msg") or fallback).strip()[:240]
        return LubanSMSError(f"{fallback}: {message}", code=code, terminal=str(code) in {"400", "401"})

    @staticmethod
    def _code(payload: dict[str, Any]) -> int:
        try:
            return int(payload.get("code", -1))
        except (TypeError, ValueError):
            return -1

    def get_number(self) -> LubanActivation:
        payload = self._request("getNumber", service_id=self.service_id)
        if self._code(payload) != 0 or not payload.get("number") or not payload.get("request_id"):
            raise self._error(payload, "LubanSMS 获取手机号失败")
        number = re.sub(r"[\s()-]", "", str(payload["number"]))
        if number.startswith("00"):
            number = "+" + number[2:]
        elif not number.startswith("+"):
            number = "+" + number
        if not re.fullmatch(r"\+[1-9]\d{6,14}", number):
            raise LubanSMSError("LubanSMS 返回的手机号不是有效国际格式", terminal=True)
        return LubanActivation(str(payload["request_id"]), number)

    def wait_code(self, request_id: str, timeout: int = 180, log: Callable[[str], None] | None = None) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            payload = self._request("getSms", request_id=request_id)
            if self._code(payload) == 0 and str(payload.get("msg") or "").lower() == "wait":
                if log:
                    log("LubanSMS waiting for code")
                time.sleep(5)
                continue
            if self._code(payload) == 0:
                candidates = extract_otp_candidates(json.dumps(payload, ensure_ascii=False))
                if candidates:
                    return str(candidates[0]["code"])
            raise self._error(payload, "LubanSMS 获取短信失败")
        raise TimeoutError("LubanSMS code timeout")

    def release(self, request_id: str) -> None:
        if not request_id:
            return
        payload = self._request("setStatus", request_id=request_id, status="reject")
        if self._code(payload) != 0:
            raise self._error(payload, "LubanSMS 释放手机号失败")
