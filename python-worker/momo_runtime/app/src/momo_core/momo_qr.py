"""Validation and metadata extraction for MoMo QR payloads."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit


def parse_qr_payload(payload: str, amount: str = "") -> dict[str, str]:
    value = str(payload or "").strip()
    if not value:
        raise ValueError("二维码内容为空")
    if len(value) > 16_384:
        raise ValueError("二维码内容过长")
    parsed_amount = str(amount or "").strip()
    merchant = ""
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        value = str(decoded.get("payload") or decoded.get("qr_payload") or decoded.get("url") or value).strip()
        parsed_amount = parsed_amount or str(decoded.get("amount") or decoded.get("am") or decoded.get("total") or "").strip()
        merchant = str(decoded.get("merchant") or decoded.get("merchant_name") or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"momo", "http", "https"}:
        # EMVCo payloads are plain text and commonly start with 000201.
        if not re.match(r"^\d{10,}", value):
            raise ValueError("不支持的 MoMo 二维码格式")
    if parsed.scheme.lower() in {"momo", "http", "https"}:
        query = parse_qs(parsed.query)
        parsed_amount = parsed_amount or next((str(query.get(key, [""])[0]).strip() for key in ("amount", "am", "total") if query.get(key)), "")
        merchant = merchant or next((str(query.get(key, [""])[0]).strip() for key in ("merchant", "merchantName", "receiver") if query.get(key)), "")
    if parsed_amount and (not re.fullmatch(r"\d{1,12}", parsed_amount) or int(parsed_amount) <= 0):
        raise ValueError("二维码金额必须是大于 0 的 VND 整数")
    return {"payload": value, "amount": parsed_amount, "merchant": merchant}
