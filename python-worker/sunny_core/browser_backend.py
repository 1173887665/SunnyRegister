from __future__ import annotations

import ctypes
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .proxy import playwright_proxy


@dataclass
class RegistrationBrowserSession:
    backend: str
    browser: Any
    context: Any


def _close_with_timeout(callback: Callable[[], Any], timeout: float) -> tuple[bool, Exception | None]:
    """Run Playwright cleanup on its owning thread and report slow teardown.

    Playwright's sync API is backed by a greenlet bound to the thread that
    created it. Running ``context.close`` or ``Camoufox.__exit__`` in a helper
    thread raises ``Cannot switch to a different thread`` and can strand the
    whole registration batch. The worker-level watchdog still handles a
    genuinely blocked teardown; this helper keeps the API thread-safe and
    records when cleanup exceeds the configured diagnostic threshold.
    """
    started = time.monotonic()
    try:
        callback()
    except Exception as exc:  # cleanup must never escape the worker
        return True, exc
    elapsed = time.monotonic() - started
    if elapsed > max(0.1, float(timeout)):
        return False, TimeoutError(f"browser cleanup exceeded {timeout:.0f}s ({elapsed:.1f}s)")
    return True, None


def camoufox_runtime_error() -> str:
    """Return a concise container runtime error before a task can get stuck."""
    if not sys.platform.startswith("linux") or os.getenv("SUNNY_CONTAINERIZED", "").lower() not in {"1", "true", "yes"}:
        return ""
    missing: list[str] = []
    for library in ("libgtk-3.so.0", "libx11-xcb.so.1", "libasound.so.2"):
        try:
            ctypes.CDLL(library)
        except OSError:
            missing.append(library)
    if not missing:
        return ""
    return (
        "Camoufox Linux runtime dependencies are missing: "
        + ", ".join(missing)
        + ". Rebuild the python-worker image from the current Dockerfile."
    )


@contextmanager
def open_registration_browser(
    *,
    headless: bool,
    proxy_url: str,
    fingerprint: Any,
    log: Callable[[str], None],
    storage_state: dict[str, Any] | None = None,
) -> Iterator[RegistrationBrowserSession]:
    """Open one isolated registration context.

    Background registration uses Camoufox instead of Chromium headless. The
    visible mode intentionally remains Chromium so existing local workflows
    and manual challenge handling keep their current behavior.
    """

    if headless:
        runtime_error = camoufox_runtime_error()
        if runtime_error:
            raise RuntimeError(runtime_error)
        try:
            from camoufox.sync_api import Camoufox  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "后台浏览器需要 Camoufox。请重新安装 python-worker 依赖并执行 "
                "`python -m camoufox fetch`。"
            ) from exc

        launch_options: dict[str, Any] = {
            "headless": "virtual" if os.getenv("SUNNY_CONTAINERIZED", "").lower() in {"1", "true", "yes"} else True,
            "block_webrtc": True,
            "humanize": True,
            "locale": [fingerprint.locale, *fingerprint.languages[1:]],
            "os": ["windows", "linux"],
        }
        proxy = playwright_proxy(proxy_url)
        if proxy:
            launch_options["proxy"] = proxy
            launch_options["geoip"] = True

        manager = Camoufox(**launch_options)
        browser = None
        context = None
        try:
            browser = manager.__enter__()
        except Exception as exc:
            raise RuntimeError(f"Camoufox browser startup failed: {str(exc)[:500]}") from exc
        try:
            context_options: dict[str, Any] = {
                "no_viewport": True,
                "locale": fingerprint.locale,
                "timezone_id": fingerprint.timezone,
                # Registration traffic can traverse rotating HTTPS proxies
                # that terminate TLS with their own certificate. Keep the
                # browser session usable while the proxy scheduler handles
                # endpoint health and rotation.
                "ignore_https_errors": True,
            }
            if storage_state:
                context_options["storage_state"] = storage_state
            context = browser.new_context(
                **context_options,
            )
            log("[认证] 已启动 Camoufox 后台浏览器与隔离无痕上下文")
            yield RegistrationBrowserSession("camoufox", browser, context)
        finally:
            connected = False
            if browser is not None:
                try:
                    connected = bool(browser.is_connected())
                except Exception:
                    connected = False
            cleanup_timeout = max(1.0, float(os.getenv("SUNNY_BROWSER_CLEANUP_TIMEOUT_SECONDS", "10")))
            if context is not None and connected:
                completed, error = _close_with_timeout(context.close, cleanup_timeout)
                if not completed or error is not None:
                    log(f"[认证] Camoufox Context 关闭异常，跳过阻塞回收：{str(error)[:180]}")
                    connected = False
            if not connected:
                # Camoufox.__exit__ calls browser.close() before stopping the
                # Playwright transport. Calling close again on a dead driver can
                # block forever, so skip that duplicate close and stop transport.
                manager.browser = None
            completed, error = _close_with_timeout(lambda: manager.__exit__(None, None, None), cleanup_timeout)
            if not completed or error is not None:
                log(f"[认证] Camoufox 驱动回收异常，已放弃阻塞清理：{str(error)[:180]}")
        return

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"Playwright is required for visible registration: {exc}") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            proxy=playwright_proxy(proxy_url),
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--lang={fingerprint.locale}",
                f"--window-size={fingerprint.outer_width},{fingerprint.outer_height}",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context_options: dict[str, Any] = {
            "user_agent": fingerprint.user_agent,
            "locale": fingerprint.locale,
            "timezone_id": fingerprint.timezone,
            "ignore_https_errors": True,
            "viewport": {"width": fingerprint.viewport_width, "height": fingerprint.viewport_height},
            "screen": {"width": fingerprint.screen_width, "height": fingerprint.screen_height},
            "device_scale_factor": fingerprint.device_scale_factor,
            "is_mobile": False,
            "has_touch": False,
        }
        if storage_state:
            context_options["storage_state"] = storage_state
        context = browser.new_context(**context_options)
        try:
            yield RegistrationBrowserSession("chromium", browser, context)
        finally:
            try:
                context.close()
            finally:
                browser.close()
