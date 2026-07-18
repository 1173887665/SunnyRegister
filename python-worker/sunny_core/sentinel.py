from __future__ import annotations

import base64
import json
import os
import random
import secrets
import time
import uuid
from typing import Any


SENTINEL_BASE = os.environ.get("SENTINEL_BASE_URL", "https://sentinel.openai.com")
SENTINEL_SDK_VERSION = os.environ.get("SENTINEL_SDK_VERSION", "20260124ceb8")
SENTINEL_FRAME_VERSION = os.environ.get("SENTINEL_FRAME_VERSION", "20260219f9f6")
SENTINEL_SDK_URL = f"{SENTINEL_BASE}/sentinel/{SENTINEL_SDK_VERSION}/sdk.js"
SENTINEL_REQ_URL = f"{SENTINEL_BASE}/backend-api/sentinel/req"
SENTINEL_FRAME_URL = f"{SENTINEL_BASE}/backend-api/sentinel/frame.html?sv={SENTINEL_FRAME_VERSION}"


def generate_datadog_trace_headers() -> dict[str, str]:
    trace_hex = secrets.token_hex(8).rjust(16, "0")
    parent_hex = secrets.token_hex(8).rjust(16, "0")
    return {
        "traceparent": f"00-0000000000000000{trace_hex}-{parent_hex}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": str(int(parent_hex, 16)),
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(int(trace_hex, 16)),
    }


class SentinelTokenGenerator:
    def __init__(self, device_id: str, user_agent: str):
        self.device_id = device_id or str(uuid.uuid4())
        self.user_agent = user_agent
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a32(text: str) -> str:
        value = 2166136261
        for char in text:
            value ^= ord(char)
            value = (value * 16777619) & 0xFFFFFFFF
        value ^= value >> 16
        value = (value * 2246822507) & 0xFFFFFFFF
        value ^= value >> 13
        value = (value * 3266489909) & 0xFFFFFFFF
        value ^= value >> 16
        return f"{value & 0xFFFFFFFF:08x}"

    @staticmethod
    def _b64(data: Any) -> str:
        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.b64encode(payload).decode("ascii")

    def _config(self) -> list[Any]:
        perf_now = 1000 + random.random() * 49000
        return [
            "1920x1080",
            time.strftime("%a, %d %b %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()),
            4294705152,
            random.random(),
            self.user_agent,
            SENTINEL_SDK_URL,
            None,
            None,
            "ja-JP",
            "ja-JP,ja",
            random.random(),
            "webkitTemporaryStorage√undefined",
            "location",
            "Object",
            perf_now,
            self.sid,
            "",
            random.choice([4, 8, 12, 16]),
            int(time.time() * 1000 - perf_now),
        ]

    def requirements_token(self) -> str:
        config = self._config()
        config[3] = 1
        config[9] = round(5 + random.random() * 45)
        return "gAAAAAC" + self._b64(config)

    def proof_token(self, seed: str, difficulty: str) -> str:
        config = self._config()
        started = int(time.time() * 1000)
        target = str(difficulty or "0")
        for nonce in range(500000):
            config[3] = nonce
            config[9] = round(int(time.time() * 1000) - started)
            encoded = self._b64(config)
            if self._fnv1a32((seed or "") + encoded)[: len(target)] <= target:
                return "gAAAAAB" + encoded + "~S"
        return "gAAAAAB" + self._b64(None)


def browser_fetch(
    page,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    return page.evaluate(
        """async ({url, method, headers, body, timeoutMs}) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), timeoutMs);
          try {
            const response = await fetch(url, {
              method, headers: headers || {},
              body: body === null ? undefined : body,
              credentials: 'include', redirect: 'follow', signal: controller.signal,
            });
            const text = await response.text();
            let data = null;
            try { data = JSON.parse(text); } catch (_) {}
            return {ok: response.ok, status: response.status, url: response.url || url, text, data};
          } catch (error) {
            return {ok: false, status: 0, url, text: String(error && error.message || error), data: null};
          } finally { clearTimeout(timer); }
        }""",
        {"url": url, "method": method, "headers": headers or {}, "body": body, "timeoutMs": timeout_ms},
    )


def build_sentinel_token(page, device_id: str, flow: str, user_agent: str) -> str:
    generator = SentinelTokenGenerator(device_id, user_agent)
    request_body = json.dumps(
        {"p": generator.requirements_token(), "id": device_id, "flow": flow},
        separators=(",", ":"),
    )
    result = browser_fetch(
        page,
        SENTINEL_REQ_URL,
        method="POST",
        headers={
            "accept": "*/*",
            "accept-language": "ja-JP,ja;q=0.9",
            "content-type": "text/plain;charset=UTF-8",
            "origin": SENTINEL_BASE,
            "referer": SENTINEL_FRAME_URL,
        },
        body=request_body,
    )
    data = result.get("data") if isinstance(result, dict) else None
    data = data if isinstance(data, dict) else {}
    challenge = str(data.get("token") or "").strip()
    if not challenge:
        return ""
    proof = data.get("proofofwork") if isinstance(data.get("proofofwork"), dict) else {}
    if proof.get("required") and proof.get("seed"):
        value = generator.proof_token(str(proof.get("seed")), str(proof.get("difficulty") or "0"))
    else:
        value = generator.requirements_token()
    return json.dumps(
        {"p": value, "t": "", "c": challenge, "id": device_id, "flow": flow},
        separators=(",", ":"),
    )
