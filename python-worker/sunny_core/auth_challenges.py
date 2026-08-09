from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import time


def normalize_totp_secret(value: str) -> str:
    secret = re.sub(r"[\s=]+", "", str(value or "")).upper()
    if not re.fullmatch(r"[A-Z2-7]{16,128}", secret):
        raise ValueError("2FA secret must contain 16-128 Base32 characters (A-Z, 2-7)")
    return secret


def generate_totp(value: str, timestamp: float | None = None) -> str:
    secret = normalize_totp_secret(value)
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int((time.time() if timestamp is None else timestamp) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"
