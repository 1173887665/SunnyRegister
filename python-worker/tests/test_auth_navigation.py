from __future__ import annotations

from unittest.mock import Mock

import pytest

from sunny_core.openai_auth import _goto_auth_page


def test_auth_navigation_accepts_ns_binding_aborted_after_redirect() -> None:
    page = Mock()
    page.url = "https://chatgpt.com/"
    def redirected(*_args, **_kwargs):
        page.url = "https://auth.openai.com/log-in"
        raise RuntimeError("Page.goto: NS_BINDING_ABORTED")
    page.goto.side_effect = redirected
    logs: list[str] = []

    result = _goto_auth_page(page, "https://auth.openai.com/api/accounts/authorize", logs.append)

    assert result is None
    page.goto.assert_called_once()
    assert any("认证导航由上游重定向接管" in message for message in logs)


def test_auth_navigation_retries_when_abort_did_not_land() -> None:
    page = Mock()
    page.url = "about:blank"
    committed = object()
    page.goto.side_effect = [RuntimeError("Page.goto: NS_BINDING_ABORTED"), committed]

    result = _goto_auth_page(page, "https://auth.openai.com/api/accounts/authorize")

    assert result is committed
    assert page.goto.call_count == 2
    assert page.goto.call_args_list[1].kwargs["wait_until"] == "commit"


def test_auth_navigation_does_not_accept_unchanged_chatgpt_page() -> None:
    page = Mock()
    page.url = "https://chatgpt.com/"
    committed = object()
    page.goto.side_effect = [RuntimeError("Page.goto: NS_BINDING_ABORTED"), committed]

    result = _goto_auth_page(page, "https://auth.openai.com/api/accounts/authorize")

    assert result is committed
    assert page.goto.call_count == 2


def test_auth_navigation_preserves_unrelated_failures() -> None:
    page = Mock()
    page.goto.side_effect = RuntimeError("Page.goto: net::ERR_CONNECTION_RESET")

    with pytest.raises(RuntimeError, match="ERR_CONNECTION_RESET"):
        _goto_auth_page(page, "https://auth.openai.com/api/accounts/authorize")
