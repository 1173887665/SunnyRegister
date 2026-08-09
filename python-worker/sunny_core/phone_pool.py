from __future__ import annotations

import dataclasses
import re
import time
from typing import Callable

import requests

from .otp_candidates import extract_otp_candidates


@dataclasses.dataclass
class PhoneEntry:
    number: str
    sms_url: str
    id: int = 0


def parse_phone_line(line: str) -> PhoneEntry:
    text = str(line or "").strip()
    if "----" in text:
        a, b = [p.strip() for p in text.split("----", 1)]
        if a and b.startswith("http"):
            return PhoneEntry(a, b)
    m = re.match(r"^([+\d][\d\s().-]*)\s*(https?://\S+)\s*$", text)
    if not m:
        raise ValueError("格式错误，应为 +手机号----https://接码链接")
    return PhoneEntry(m.group(1).strip(), m.group(2).strip())


def read_sms_candidates(sms_url: str, timeout: int = 20) -> list[dict]:
    response = requests.get(sms_url, timeout=timeout)
    response.raise_for_status()
    return extract_otp_candidates(response.text)


def wait_sms_code(number: str, sms_url: str, timeout: int = 180, log: Callable[[str], None] | None = None, seen_keys: set[str] | None = None) -> str:
    deadline = time.time() + timeout
    last = ""
    baseline = set(seen_keys or ())
    while time.time() < deadline:
        try:
            candidates = read_sms_candidates(sms_url, timeout=min(20, max(2, int(deadline - time.time()))))
            fresh = next((item for item in candidates if item["key"] not in baseline), None)
            baseline.update(item["key"] for item in candidates)
            if fresh:
                if log:
                    log(f"手机号 {number} 已读取到新验证码（6 位，已脱敏）")
                return str(fresh["code"])
        except Exception as exc:
            last = str(exc)
        time.sleep(5)
    raise RuntimeError(f"等待手机号 {number} 验证码超时，最后返回: {last[:240]}")
