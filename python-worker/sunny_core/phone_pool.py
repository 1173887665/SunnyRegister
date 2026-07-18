from __future__ import annotations

import dataclasses
import re
import time
from typing import Callable

import requests


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


def extract_sms_code(text: str) -> str:
    for pat in (r"(?<!\d)(\d{6})(?!\d)", r"(?<!\d)(\d{5})(?!\d)", r"(?<!\d)(\d{4})(?!\d)"):
        m = re.search(pat, text or "")
        if m:
            return m.group(1)
    return ""


def wait_sms_code(number: str, sms_url: str, timeout: int = 180, log: Callable[[str], None] | None = None) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            res = requests.get(sms_url, timeout=min(20, max(2, int(deadline - time.time()))))
            last = res.text[:500]
            code = extract_sms_code(res.text)
            if code:
                if log:
                    log(f"手机号 {number} 已读取到验证码（{len(code)} 位，已脱敏）")
                return code
        except Exception as exc:
            last = str(exc)
        time.sleep(5)
    raise RuntimeError(f"等待手机号 {number} 验证码超时，最后返回: {last[:240]}")
