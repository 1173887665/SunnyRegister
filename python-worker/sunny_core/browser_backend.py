from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .proxy import playwright_proxy


@dataclass
class RegistrationBrowserSession:
    backend: str
    browser: Any
    context: Any


@contextmanager
def open_registration_browser(
    *,
    headless: bool,
    proxy_url: str,
    fingerprint: Any,
    log: Callable[[str], None],
) -> Iterator[RegistrationBrowserSession]:
    """Open one isolated registration context.

    Background registration uses Camoufox instead of Chromium headless. The
    visible mode intentionally remains Chromium so existing local workflows
    and manual challenge handling keep their current behavior.
    """

    if headless:
        try:
            from camoufox.sync_api import Camoufox  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "后台浏览器需要 Camoufox。请重新安装 python-worker 依赖并执行 "
                "`python -m camoufox fetch`。"
            ) from exc

        launch_options: dict[str, Any] = {
            "headless": True,
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
            context = browser.new_context(
                no_viewport=True,
                locale=fingerprint.locale,
                timezone_id=fingerprint.timezone,
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
            if context is not None and connected:
                try:
                    context.close()
                except Exception as exc:
                    log(f"[认证] Camoufox Context 关闭异常，继续回收驱动：{str(exc)[:180]}")
            if not connected:
                # Camoufox.__exit__ calls browser.close() before stopping the
                # Playwright transport. Calling close again on a dead driver can
                # block forever, so skip that duplicate close and stop transport.
                manager.browser = None
            try:
                manager.__exit__(None, None, None)
            except Exception as exc:
                log(f"[认证] Camoufox 驱动回收异常：{str(exc)[:180]}")
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
        context = browser.new_context(
            user_agent=fingerprint.user_agent,
            locale=fingerprint.locale,
            timezone_id=fingerprint.timezone,
            viewport={"width": fingerprint.viewport_width, "height": fingerprint.viewport_height},
            screen={"width": fingerprint.screen_width, "height": fingerprint.screen_height},
            device_scale_factor=fingerprint.device_scale_factor,
            is_mobile=False,
            has_touch=False,
        )
        try:
            yield RegistrationBrowserSession("chromium", browser, context)
        finally:
            try:
                context.close()
            finally:
                browser.close()
