"""A small, deployable MoMo protocol gateway.

SunnyRegister speaks a provider-neutral JSON contract.  This process keeps
that contract stable while forwarding the request to the configured MoMo SDK
or official integration service.  It deliberately does not invent wallet
credentials or protocol signatures: the upstream adapter owns those details.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


ROUTES = {
    "/register/start",
    "/register/send-otp",
    "/register/verify-otp",
    "/register/profile",
    "/register/pin",
    "/device/bind",
    "/session",
    "/login",
    "/payment/scan",
    "/payment/otp",
    "/payment/confirm",
}


def _normalize_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _parse_headers(value: str) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in parsed.items()
        if str(key).strip() and item is not None and str(key).lower() not in {"host", "content-length"}
    }


def _has_header(headers: dict[str, str], name: str) -> bool:
    target = name.lower()
    return any(str(key).lower() == target for key in headers)


def _proxy(payload: dict) -> str:
    candidates = [payload.get("proxy")]
    session = payload.get("session")
    if isinstance(session, dict):
        candidates.append(session.get("proxy"))
    for value in candidates:
        if value and str(value).strip():
            return str(value).strip()
    return ""


class Adapter:
    def __init__(self) -> None:
        self.upstream = _normalize_url(os.getenv("OPAI_MOMO_ADAPTER_UPSTREAM_URL", ""))
        self.prefix = "/" + os.getenv("OPAI_MOMO_ADAPTER_UPSTREAM_PREFIX", "").strip("/") if os.getenv("OPAI_MOMO_ADAPTER_UPSTREAM_PREFIX", "").strip("/") else ""
        self.headers = _parse_headers(os.getenv("OPAI_MOMO_ADAPTER_HEADERS") or os.getenv("OPAI_MOMO_API_HEADERS"))
        # ADAPTER_TOKEN is the explicit gateway credential.  API_TOKEN is kept
        # as a fallback so a deployment using one token for the direct and
        # gateway modes does not silently drop authentication at the upstream.
        self.token = (os.getenv("OPAI_MOMO_ADAPTER_TOKEN") or os.getenv("OPAI_MOMO_API_TOKEN") or "").strip()
        try:
            self.timeout = max(5, min(300, int(os.getenv("OPAI_MOMO_ADAPTER_TIMEOUT", "60"))))
        except ValueError:
            self.timeout = 60
        try:
            self.retries = max(0, min(3, int(os.getenv("OPAI_MOMO_ADAPTER_RETRIES", "1") or 1)))
        except ValueError:
            self.retries = 1

    def health(self) -> dict:
        result = {
            "ok": bool(self.upstream),
            "service": "momo-adapter",
            "upstream_configured": bool(self.upstream),
            "timeout_sec": self.timeout,
            "routes": sorted(ROUTES),
        }
        if not self.upstream:
            return result
        reachable, message = self._probe_upstream()
        result["upstream_reachable"] = reachable
        result["message"] = message
        result["ok"] = reachable
        return result

    def _probe_upstream(self) -> tuple[bool, str]:
        """Check liveness without invoking a mutating wallet operation."""
        headers = {"Accept": "application/json", **self.headers}
        if self.token and not _has_header(headers, "Authorization"):
            headers["Authorization"] = f"Bearer {self.token}"
        for path in ("/health", "/api/health"):
            request = urllib.request.Request(f"{self.upstream}{self.prefix}{path}", method="GET", headers=headers)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request, timeout=min(self.timeout, 3)) as response:
                    status = int(getattr(response, "status", 200) or 200)
                    if status < 500:
                        return True, f"上游健康检查 HTTP {status}"
            except urllib.error.HTTPError as exc:
                # 404/405 still prove that the upstream HTTP service is alive;
                # authentication and server errors remain a failed probe.
                if exc.code in {404, 405}:
                    return True, f"上游服务可达（HTTP {exc.code}）"
                last = f"HTTP {exc.code}"
            except (OSError, urllib.error.URLError) as exc:
                last = str(exc)
        return False, f"上游健康检查失败: {locals().get('last', '未知错误')}"

    def call(self, route: str, payload: dict) -> tuple[int, dict]:
        if not self.upstream:
            return 503, {"ok": False, "error": "MoMo adapter upstream URL is not configured"}
        url = f"{self.upstream}{self.prefix}{route}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json", **self.headers}
        request_id = str(payload.get("idempotency_key") or payload.get("request_id") or "").strip()
        if request_id:
            headers["Idempotency-Key"] = request_id
        if self.token and not _has_header(headers, "Authorization"):
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        proxy = _proxy(payload)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8", "ignore") or "{}"
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        data = {"ok": False, "error": "MoMo upstream response must be a JSON object"}
                    data.setdefault("ok", int(getattr(response, "status", 200) or 200) < 400)
                    return int(getattr(response, "status", 200) or 200), data
            except urllib.error.HTTPError as exc:
                try:
                    raw_error = json.loads(exc.read().decode("utf-8", "ignore") or "{}")
                except (OSError, ValueError):
                    raw_error = {}
                data = raw_error if isinstance(raw_error, dict) else {"error": str(raw_error)}
                if 500 <= int(exc.code) < 600 and attempt < self.retries:
                    last_error = f"HTTP {exc.code}"
                    time.sleep(0.25 * (attempt + 1))
                    continue
                data.setdefault("ok", False)
                return int(exc.code), data
            except (OSError, urllib.error.URLError, ValueError) as exc:
                last_error = str(exc)
                if attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
        return 502, {"ok": False, "error": f"MoMo upstream unavailable: {last_error}"}


ADAPTER = Adapter()


def _body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        value = json.loads(handler.rfile.read(size) or b"{}") if size else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, value: dict) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path in {"/health", "/api/health"}:
            health = ADAPTER.health()
            return self.send_json(200 if health["ok"] else 503, health)
        return self.send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/") or "/"
        route = path[4:] if path.startswith("/api/") else path
        if route not in ROUTES:
            return self.send_json(404, {"ok": False, "error": "unsupported_momo_route"})
        status, data = ADAPTER.call(route, _body(self))
        return self.send_json(status, data)

    def log_message(self, *_args: object) -> None:
        return


def start_embedded(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
    threading.Thread(target=server.serve_forever, name="momo-adapter-http", daemon=True).start()
    return server


def main() -> None:
    port = int(os.getenv("OPAI_MOMO_ADAPTER_PORT", "19082"))
    server = ThreadingHTTPServer((os.getenv("OPAI_MOMO_ADAPTER_HOST", "127.0.0.1"), port), Handler)
    print(f"[momo-adapter] listening on {server.server_address[0]}:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
