from __future__ import annotations

from sunny_core.browser_traffic import BrowserTrafficConfig, BrowserTrafficOptimizer, ProxyTrafficMeter


def test_proxy_traffic_meter_counts_application_bytes_by_phase() -> None:
    meter = ProxyTrafficMeter(proxy_url="http://pool:8080", tracked_proxy=True, email="a@example.com", operation="sunny_register")
    meter.record("POST", "https://chatgpt.com/api/auth", {"content-type": "application/json"}, "{}", 200, {"content-type": "application/json"}, 128, "http")
    meter.set_phase("session_only")
    meter.record("GET", "https://chatgpt.com/api/auth/session", {}, None, 200, {}, 64, "http")
    snapshot = meter.snapshot()
    assert snapshot["requests"] == 2
    assert snapshot["total_bytes"] > 192
    assert set(snapshot["by_phase"]) == {"initial", "session_only"}


def test_proxy_traffic_meter_ignores_untracked_proxy() -> None:
    meter = ProxyTrafficMeter(proxy_url="http://pool:8080", tracked_proxy=False)
    meter.record("GET", "https://chatgpt.com/", {}, None, 200, {}, 1024)
    assert meter.snapshot()["total_bytes"] == 0


def test_browser_traffic_config_has_conservative_bounds() -> None:
    config = BrowserTrafficConfig.from_value({"cache_ttl_hours": 999, "cache_max_mib": 1, "cache_object_max_mib": 999})
    assert config.cache_ttl_hours == 168
    assert config.cache_max_mib == 16
    assert config.cache_object_max_mib == 32


def test_browser_optimizer_prioritizes_security_and_auth_requests() -> None:
    meter = ProxyTrafficMeter(tracked_proxy=True)
    optimizer = BrowserTrafficOptimizer(meter)
    assert not optimizer._should_block("https://auth.openai.com/assets/app.js", "script", "GET")
    assert not optimizer._should_block("https://challenges.cloudflare.com/turnstile/v0/api.js", "script", "GET")
    assert optimizer._should_block("https://chatgpt.com/assets/font.woff2", "font", "GET")
    assert not optimizer._should_block("https://chatgpt.com/api/auth/session", "xhr", "GET")


def test_browser_optimizer_session_only_suppresses_chatgpt_shell() -> None:
    meter = ProxyTrafficMeter(tracked_proxy=True)
    optimizer = BrowserTrafficOptimizer(meter)
    optimizer.activate_session_only()
    assert optimizer._should_block("https://chatgpt.com/_next/static/app.js", "script", "GET")
    assert not optimizer._should_block("https://auth.openai.com/oauth/authorize", "document", "GET")
