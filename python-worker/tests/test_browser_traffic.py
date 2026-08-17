from __future__ import annotations

import json

from sunny_core.browser_traffic import (
    BrowserTrafficConfig,
    BrowserTrafficOptimizer,
    ProxyTrafficMeter,
    _make_session_request_hook,
    suspend_http_traffic_hook,
    use_traffic_meter,
)


def test_session_hooks_keep_requests_and_curl_originals_separate() -> None:
    calls: list[str] = []

    def requests_original(_session, _method, _url, **_kwargs):
        calls.append("requests")
        return type("Response", (), {"url": "https://example.test", "status_code": 200, "headers": {}, "content": b""})()

    def curl_original(_session, _method, _url, **_kwargs):
        calls.append("curl")
        return type("Response", (), {"url": "https://example.test", "status_code": 200, "headers": {}, "content": b""})()

    requests_hook = _make_session_request_hook(requests_original)
    curl_hook = _make_session_request_hook(curl_original)
    requests_hook(object(), "GET", "https://example.test")
    curl_hook(object(), "GET", "https://example.test")
    assert calls == ["requests", "curl"]


def test_proxy_traffic_meter_counts_application_bytes_by_phase() -> None:
    meter = ProxyTrafficMeter(proxy_url="http://pool:8080", tracked_proxy=True, email="a@example.com", operation="sunny_register")
    meter.record("POST", "https://chatgpt.com/api/auth", {"content-type": "application/json"}, "{}", 200, {"content-type": "application/json"}, 128, "http")
    meter.set_phase("session_only")
    meter.record("GET", "https://chatgpt.com/api/auth/session", {}, None, 200, {}, 64, "http")
    snapshot = meter.snapshot()
    assert snapshot["requests"] == 2
    assert snapshot["total_bytes"] > 192
    assert set(snapshot["by_phase"]) == {"initial", "session_only"}
    assert snapshot["by_host"]["chatgpt.com"]["requests"] == 2
    assert "chatgpt.com/api/auth/session" in snapshot["by_path"]


def test_proxy_traffic_meter_ignores_untracked_proxy() -> None:
    meter = ProxyTrafficMeter(proxy_url="http://pool:8080", tracked_proxy=False)
    meter.record("GET", "https://chatgpt.com/", {}, None, 200, {}, 1024)
    assert meter.snapshot()["total_bytes"] == 0


def test_http_hook_can_be_suspended_for_explicit_protocol_metering() -> None:
    meter = ProxyTrafficMeter(proxy_url="http://pool:8080", tracked_proxy=True)
    session = type("Session", (), {"headers": {}, "proxies": {"https": "http://pool:8080"}})()

    def original(_session, _method, _url, **_kwargs):
        return type("Response", (), {"url": "https://example.test", "status_code": 200, "headers": {}, "content": b"ok"})()

    request = _make_session_request_hook(original)
    with use_traffic_meter(meter), suspend_http_traffic_hook():
        request(session, "GET", "https://example.test")

    assert meter.snapshot()["requests"] == 0


def test_browser_traffic_config_has_conservative_bounds() -> None:
    config = BrowserTrafficConfig.from_value({"cache_ttl_hours": 999, "cache_max_mib": 1, "cache_object_max_mib": 999})
    assert config.cache_ttl_hours == 168
    assert config.cache_max_mib == 16
    assert config.cache_object_max_mib == 32
    assert BrowserTrafficConfig.from_value({}).cache_ttl_hours == 168


def test_browser_optimizer_prioritizes_security_and_auth_requests() -> None:
    meter = ProxyTrafficMeter(tracked_proxy=True)
    optimizer = BrowserTrafficOptimizer(meter)
    assert not optimizer._should_block("https://auth.openai.com/assets/app.js", "script", "GET")
    assert not optimizer._should_block("https://challenges.cloudflare.com/turnstile/v0/api.js", "script", "GET")
    assert not optimizer._should_block("https://chatgpt.com/cdn-cgi/challenge-platform/a.js", "script", "GET")
    assert optimizer._should_block("https://auth.openai.com/assets/font.woff2", "font", "GET")
    assert optimizer._should_block("https://auth.openai.com/rum", "xhr", "GET")
    assert optimizer._should_block("https://statsigapi.net/v1/events", "xhr", "POST")
    assert optimizer._should_block("https://chatgpt.com/assets/font.woff2", "font", "GET")
    assert not optimizer._should_block("https://chatgpt.com/api/auth/session", "xhr", "GET")


def test_browser_optimizer_session_only_suppresses_chatgpt_shell() -> None:
    meter = ProxyTrafficMeter(tracked_proxy=True)
    optimizer = BrowserTrafficOptimizer(meter)
    optimizer.activate_session_only()
    assert optimizer._should_block("https://chatgpt.com/_next/static/app.js", "script", "GET")
    assert optimizer._should_block("https://chatgpt.com/backend-api/models", "xhr", "GET")
    assert not optimizer._should_block("https://chatgpt.com/api/auth/session", "xhr", "GET")
    assert not optimizer._should_block("https://auth.openai.com/oauth/authorize", "document", "GET")


def test_browser_static_cache_writes_separate_files_and_excludes_replay_from_proxy_meter(tmp_path) -> None:
    meter = ProxyTrafficMeter(proxy_url="http://pool:8080", tracked_proxy=True)
    optimizer = BrowserTrafficOptimizer(meter)
    optimizer._cache_dir = tmp_path
    url = "https://assets.auth.openai.com/assets/app.js"
    headers = {
        "cache-control": "public, max-age=31536000, immutable",
        "content-type": "application/javascript",
        "content-encoding": "br",
        "content-length": "321",
    }

    assert optimizer._cacheable(url, "script", "GET", {"cookie": "account-session"})
    assert not optimizer._cacheable("https://auth.openai.com/service-worker.js", "script", "GET", {})
    optimizer._store_cache(url, 200, headers, b"console.log('cached')", 321)
    body_path, meta_path = optimizer._cache_paths(url)
    assert body_path.read_bytes() == b"console.log('cached')"
    assert meta_path.exists()
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert metadata["expires_at"] - metadata["created_at"] == 168 * 3600

    class FakeContext:
        handler = None
        listener = None

        def route(self, _pattern, handler):
            self.handler = handler

        def on(self, _event, listener):
            self.listener = listener

        def unroute(self, _pattern, _handler):
            return None

        def remove_listener(self, _event, _listener):
            return None

    class FakeRequest:
        method = "GET"
        resource_type = "script"
        post_data = None

        def __init__(self):
            self.url = url
            self.headers = {"cookie": "account-session"}

    class FakeRoute:
        def __init__(self, context, request):
            self.context = context
            self.request = request
            self.fulfilled = None

        def fulfill(self, **kwargs):
            self.fulfilled = kwargs
            response = type(
                "Response",
                (),
                {"request": self.request, "headers": kwargs.get("headers", {}), "status": kwargs.get("status", 200), "url": self.request.url},
            )()
            self.context.listener(response)

        def continue_(self):
            raise AssertionError("cache hit must not continue to the network")

    context = FakeContext()
    optimizer.attach(context)
    route = FakeRoute(context, FakeRequest())
    context.handler(route)

    assert route.fulfilled["body"] == b"console.log('cached')"
    assert "content-encoding" not in route.fulfilled["headers"]
    assert meter.snapshot()["requests"] == 0
    assert optimizer.snapshot()["cache_hits"] == 1
    assert optimizer.snapshot()["cache_saved_bytes"] == 321


def test_browser_static_cache_rejects_account_varying_response(tmp_path) -> None:
    optimizer = BrowserTrafficOptimizer(ProxyTrafficMeter(tracked_proxy=True))
    optimizer._cache_dir = tmp_path
    url = "https://cdn.oaistatic.com/assets/private.js"
    optimizer._store_cache(
        url,
        200,
        {"cache-control": "public, max-age=3600", "content-type": "application/javascript", "vary": "Cookie"},
        b"private",
    )
    body_path, meta_path = optimizer._cache_paths(url)
    assert not body_path.exists()
    assert not meta_path.exists()


def test_browser_optimizer_attach_failure_is_fail_open() -> None:
    class BrokenContext:
        def route(self, _pattern, _handler):
            raise RuntimeError("route unsupported")

    optimizer = BrowserTrafficOptimizer(ProxyTrafficMeter(tracked_proxy=True))
    optimizer.attach(BrokenContext())
    assert optimizer.snapshot()["cache_write_errors"] == 1
