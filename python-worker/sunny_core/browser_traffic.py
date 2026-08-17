from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit


_CURRENT_METER: contextvars.ContextVar["ProxyTrafficMeter | None"] = contextvars.ContextVar(
    "sunny_proxy_traffic_meter", default=None
)
_HOOKS_LOCK = threading.Lock()
_HOOKS_INSTALLED = False


def _byte_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (dict, list, tuple)):
        try:
            return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass
    return len(str(value).encode("utf-8"))


def _headers_bytes(headers: Any) -> int:
    if not headers:
        return 0
    try:
        return sum(len(str(k).encode()) + len(str(v).encode()) + 4 for k, v in headers.items()) + 2
    except Exception:
        return 0


def _response_body_bytes(response: Any) -> int:
    headers = getattr(response, "headers", {}) or {}
    content_length = str(headers.get("content-length") or headers.get("Content-Length") or "").strip()
    if content_length.isdigit():
        return int(content_length)
    try:
        return len(response.content or b"")
    except Exception:
        return 0


def _proxy_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(item or "") for item in value.values()]
    return []


@dataclass
class ProxyTrafficMeter:
    """Account application-layer bytes sent through one selected proxy pool entry."""

    proxy_url: str = ""
    tracked_proxy: bool = False
    email: str = ""
    operation: str = ""
    requests: int = 0
    request_header_bytes: int = 0
    request_body_bytes: int = 0
    response_header_bytes: int = 0
    response_body_bytes: int = 0
    by_phase: dict[str, int] = field(default_factory=dict)
    by_kind: dict[str, int] = field(default_factory=dict)
    _phase: str = "initial"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_phase(self, phase: str) -> None:
        self._phase = str(phase or "initial")

    def matches_proxy(self, proxy: Any) -> bool:
        if not self.tracked_proxy or not self.proxy_url:
            return False
        target = self.proxy_url.rstrip("/")
        return any(str(value or "").rstrip("/") == target for value in _proxy_values(proxy))

    def record(
        self,
        method: str,
        url: str,
        request_headers: Any = None,
        request_body: Any = None,
        response_status: int = 0,
        response_headers: Any = None,
        response_body_bytes: int = 0,
        kind: str = "http",
    ) -> None:
        if not self.tracked_proxy:
            return
        parsed = urlsplit(str(url or ""))
        target = f"{parsed.path or '/'}{('?' + parsed.query) if parsed.query else ''}"
        req_headers = len(f"{str(method or 'GET').upper()} {target} HTTP/1.1\r\n".encode()) + _headers_bytes(request_headers)
        req_body = _byte_count(request_body)
        resp_headers = len(f"HTTP/1.1 {int(response_status or 0):03d}\r\n".encode()) + _headers_bytes(response_headers)
        total = req_headers + req_body + resp_headers + max(0, int(response_body_bytes or 0))
        with self._lock:
            self.requests += 1
            self.request_header_bytes += req_headers
            self.request_body_bytes += req_body
            self.response_header_bytes += resp_headers
            self.response_body_bytes += max(0, int(response_body_bytes or 0))
            self.by_phase[self._phase] = self.by_phase.get(self._phase, 0) + total
            self.by_kind[kind] = self.by_kind.get(kind, 0) + total

    def snapshot(self) -> dict[str, Any]:
        total = self.request_header_bytes + self.request_body_bytes + self.response_header_bytes + self.response_body_bytes
        return {
            "measurement": "estimated_http_application_bytes_excluding_tls_tcp_overhead",
            "tracked_proxy": bool(self.tracked_proxy),
            "proxy": self.proxy_url,
            "operation": self.operation,
            "requests": self.requests,
            "request_header_bytes": self.request_header_bytes,
            "request_body_bytes": self.request_body_bytes,
            "response_header_bytes": self.response_header_bytes,
            "response_body_bytes": self.response_body_bytes,
            "total_bytes": total,
            "by_phase": dict(self.by_phase),
            "by_kind": dict(self.by_kind),
        }


@contextlib.contextmanager
def use_traffic_meter(meter: ProxyTrafficMeter) -> Iterator[ProxyTrafficMeter]:
    install_http_hooks()
    token = _CURRENT_METER.set(meter)
    try:
        yield meter
    finally:
        _CURRENT_METER.reset(token)


def current_traffic_meter() -> ProxyTrafficMeter | None:
    return _CURRENT_METER.get()


def _hooked_request(original, session, method: str, url: str, kwargs: dict[str, Any]):
    response = original(session, method, url, **kwargs)
    meter = current_traffic_meter()
    proxy = kwargs.get("proxies") or getattr(session, "proxies", None)
    if meter and meter.matches_proxy(proxy):
        headers = dict(getattr(session, "headers", {}) or {})
        headers.update(dict(kwargs.get("headers") or {}))
        meter.record(
            method,
            str(getattr(response, "url", "") or url),
            headers,
            kwargs.get("data") if kwargs.get("data") is not None else kwargs.get("json"),
            int(getattr(response, "status_code", 0) or 0),
            getattr(response, "headers", None),
            _response_body_bytes(response),
            "http",
        )
    return response


def install_http_hooks() -> None:
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    with _HOOKS_LOCK:
        if _HOOKS_INSTALLED:
            return
        try:
            import requests.sessions

            original = requests.sessions.Session.request
            if not getattr(original, "_sunny_traffic_hook", False):
                def requests_request(session, method, url, **kwargs):
                    return _hooked_request(original, session, method, url, kwargs)
                requests_request._sunny_traffic_hook = True
                requests.sessions.Session.request = requests_request
        except Exception:
            pass
        try:
            from curl_cffi import requests as curl_requests

            original = curl_requests.Session.request
            if not getattr(original, "_sunny_traffic_hook", False):
                def curl_request(session, method, url, **kwargs):
                    return _hooked_request(original, session, method, url, kwargs)
                curl_request._sunny_traffic_hook = True
                curl_requests.Session.request = curl_request
        except Exception:
            pass
        _HOOKS_INSTALLED = True


@dataclass
class BrowserTrafficConfig:
    enabled: bool = True
    block_heavy_resources: bool = True
    static_cache_enabled: bool = True
    cache_ttl_hours: int = 24
    cache_max_mib: int = 256
    cache_object_max_mib: int = 8

    @classmethod
    def from_value(cls, value: Any) -> "BrowserTrafficConfig":
        raw = value if isinstance(value, dict) else {}
        return cls(
            enabled=raw.get("enabled") is not False,
            block_heavy_resources=raw.get("block_heavy_resources") is not False,
            static_cache_enabled=raw.get("static_cache_enabled") is not False,
            cache_ttl_hours=max(1, min(int(raw.get("cache_ttl_hours") or 24), 168)),
            cache_max_mib=max(16, min(int(raw.get("cache_max_mib") or 256), 2048)),
            cache_object_max_mib=max(1, min(int(raw.get("cache_object_max_mib") or 8), 32)),
        )


class BrowserTrafficOptimizer:
    _security_hosts = {
        "auth.openai.com", "sentinel.openai.com", "challenges.cloudflare.com",
        "client-api.arkoselabs.com", "arkose.com", "hcaptcha.com", "www.hcaptcha.com",
        "recaptcha.net", "www.recaptcha.net", "www.google.com",
    }
    _static_hosts = {"auth.openai.com", "chatgpt.com", "cdn.oaistatic.com"}
    _heavy_types = {"image", "font", "media", "manifest"}
    _telemetry_markers = ("/telemetry", "/analytics", "/rum", "/events", "sentry.io")

    def __init__(self, meter: ProxyTrafficMeter, config: BrowserTrafficConfig | dict[str, Any] | None = None):
        self.meter = meter
        self.config = config if isinstance(config, BrowserTrafficConfig) else BrowserTrafficConfig.from_value(config)
        self.session_only = False
        self._cache_dir = Path(os.getenv("SUNNY_BROWSER_CACHE_DIR") or (Path(tempfile.gettempdir()) / "sunnyregister-browser-static"))
        self._cache_lock = threading.Lock()
        self._handlers: list[tuple[Any, Any]] = []
        self._response_listeners: list[tuple[Any, Any]] = []
        self._recorded_browser_requests: set[int] = set()

    def attach(self, context: Any) -> None:
        if not self.config.enabled:
            return

        def handle(route: Any) -> None:
            request = route.request
            url = str(getattr(request, "url", "") or "")
            kind = str(getattr(request, "resource_type", "") or "")
            method = str(getattr(request, "method", "GET") or "GET").upper()
            if self._should_block(url, kind, method):
                route.abort("blockedbyclient")
                return
            if self._cacheable(url, kind, method, getattr(request, "headers", {})) and self.config.static_cache_enabled:
                if self._fulfill_cache(route, url):
                    return
                try:
                    self._recorded_browser_requests.add(id(request))
                    response = route.fetch()
                    body = response.body()
                    headers = dict(response.headers or {})
                    self.meter.record(method, url, getattr(request, "headers", {}), getattr(request, "post_data", None), response.status, headers, len(body), "browser")
                    self._store_cache(url, response.status, headers, body)
                    route.fulfill(response=response)
                    return
                except Exception:
                    self._recorded_browser_requests.discard(id(request))
                    # The optimizer must never turn a browser request failure into a registration failure.
                    try:
                        route.continue_()
                    except Exception:
                        pass
                    return
            try:
                route.continue_()
            except Exception:
                pass

        context.route("**/*", handle)
        self._handlers.append((context, handle))

        def on_response(response: Any) -> None:
            request = getattr(response, "request", None)
            if request is None or id(request) in self._recorded_browser_requests:
                self._recorded_browser_requests.discard(id(request))
                return
            try:
                headers = dict(response.headers or {})
                content_length = str(headers.get("content-length") or "").strip()
                body_length = int(content_length) if content_length.isdigit() else len(response.body())
                self.meter.record(
                    str(getattr(request, "method", "GET") or "GET"),
                    str(getattr(response, "url", "") or getattr(request, "url", "")),
                    getattr(request, "headers", {}),
                    getattr(request, "post_data", None),
                    int(getattr(response, "status", 0) or 0),
                    headers,
                    body_length,
                    "browser",
                )
            except Exception:
                pass

        try:
            context.on("response", on_response)
            self._response_listeners.append((context, on_response))
        except Exception:
            pass

    def activate_session_only(self) -> None:
        self.session_only = True
        self.meter.set_phase("session_only")

    def detach(self) -> None:
        for context, handler in self._handlers:
            try:
                context.unroute("**/*", handler)
            except Exception:
                pass
        self._handlers.clear()
        for context, listener in self._response_listeners:
            try:
                context.remove_listener("response", listener)
            except Exception:
                pass
        self._response_listeners.clear()
        self._recorded_browser_requests.clear()

    def _should_block(self, url: str, resource_type: str, method: str) -> bool:
        if not self.config.block_heavy_resources or method != "GET":
            return False
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = parts.path.lower()
        if self._is_security(url) or host in {"auth.openai.com", "sentinel.openai.com"}:
            return False
        if resource_type in self._heavy_types:
            return True
        if self.session_only and host == "chatgpt.com" and resource_type in {"script", "stylesheet"} and not path.startswith("/api/"):
            return True
        if host not in self._static_hosts and any(marker in url.lower() for marker in self._telemetry_markers):
            return True
        return False

    def _is_security(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return host in self._security_hosts or any(marker in host for marker in ("arkose", "hcaptcha", "recaptcha")) or "turnstile" in parts.path.lower()

    def _cacheable(self, url: str, resource_type: str, method: str, request_headers: Any = None) -> bool:
        parts = urlsplit(url)
        headers = {str(key).lower() for key in (request_headers or {})}
        return (
            method == "GET"
            and (parts.hostname or "").lower() in self._static_hosts
            and resource_type in {"script", "stylesheet"}
            and not self._is_security(url)
            and "cookie" not in headers
            and "authorization" not in headers
        )

    def _cache_key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = self._cache_key(url)
        return self._cache_dir / f"{key}.body", self._cache_dir / f"{key}.json"

    def _fulfill_cache(self, route: Any, url: str) -> bool:
        body_path, meta_path = self._cache_paths(url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if time.time() - float(meta.get("created_at", 0)) > self.config.cache_ttl_hours * 3600:
                return False
            body = body_path.read_bytes()
            headers = dict(meta.get("headers") or {})
            for key in ("content-encoding", "content-length", "transfer-encoding"):
                headers.pop(key, None)
            route.fulfill(status=int(meta.get("status", 200)), headers=headers, body=body)
            return True
        except Exception:
            return False

    def _store_cache(self, url: str, status: int, headers: dict[str, Any], body: bytes) -> None:
        if status != 200 or len(body) > self.config.cache_object_max_mib * 1024 * 1024:
            return
        lower = {str(k).lower(): str(v) for k, v in headers.items()}
        cache_control = lower.get("cache-control", "").lower()
        if "set-cookie" in lower or "private" in cache_control or "no-store" in cache_control or "no-cache" in cache_control:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            body_path, meta_path = self._cache_paths(url)
            with self._cache_lock:
                tmp_body = body_path.with_suffix(".tmp")
                tmp_meta = meta_path.with_suffix(".tmp")
                tmp_body.write_bytes(body)
                cache_headers = {str(k): str(v) for k, v in headers.items() if str(k).lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
                tmp_meta.write_text(json.dumps({"created_at": time.time(), "status": status, "headers": cache_headers}, ensure_ascii=False), encoding="utf-8")
                tmp_body.replace(body_path)
                tmp_meta.replace(meta_path)
                self._prune_cache()
        except Exception:
            pass

    def _prune_cache(self) -> None:
        limit = self.config.cache_max_mib * 1024 * 1024
        entries: list[tuple[float, int, Path, Path]] = []
        total = 0
        for body_path in self._cache_dir.glob("*.body"):
            meta_path = body_path.with_suffix(".json")
            try:
                size = body_path.stat().st_size
                entries.append((body_path.stat().st_mtime, size, body_path, meta_path))
                total += size
            except OSError:
                continue
        if total <= limit:
            return
        for _, size, body_path, meta_path in sorted(entries):
            if total <= limit:
                break
            try:
                body_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                total -= size
            except OSError:
                pass
