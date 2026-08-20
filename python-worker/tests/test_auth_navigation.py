from __future__ import annotations

from unittest.mock import Mock

import pytest

from sunny_core.mailbox import MailAccount
from sunny_core.openai_auth import OpenAIEmailRegisterFlow, _goto_auth_page, _goto_chatgpt_page


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


def test_auth_navigation_accepts_ns_error_abort_after_redirect() -> None:
    page = Mock()
    page.url = "https://chatgpt.com/"

    def redirected(*_args, **_kwargs):
        page.url = "https://auth.openai.com/log-in"
        raise RuntimeError("Page.goto: NS_ERROR_ABORT")

    page.goto.side_effect = redirected

    assert _goto_auth_page(page, "https://auth.openai.com/api/accounts/authorize") is None
    page.goto.assert_called_once()


def test_chatgpt_navigation_retries_transient_ssl_error() -> None:
    page = Mock()
    response = object()
    page.goto.side_effect = [RuntimeError("Page.goto: SSL_ERROR_UNKNOWN"), response]

    assert _goto_chatgpt_page(page) is response
    assert page.goto.call_count == 2
    page.wait_for_timeout.assert_called_once_with(600)


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


def test_email_step_waits_when_prefilled_input_is_disabled() -> None:
    logs: list[str] = []
    account = MailAccount("user@icloud.com", "", "", "", "raw")
    flow = OpenAIEmailRegisterFlow(account, "", True, logs.append)
    email_input = Mock()
    email_input.input_value.return_value = account.email
    email_input.is_enabled.return_value = False
    email_input.is_editable.return_value = False
    flow._visible_inputs = Mock(return_value=[email_input])
    flow._click_continue = Mock()

    assert flow._fill_email_if_visible(Mock()) is False
    assert flow._fill_email_if_visible(Mock()) is False

    email_input.fill.assert_not_called()
    flow._click_continue.assert_not_called()
    assert sum("邮箱已提交" in message for message in logs) == 1


def test_email_step_skips_duplicate_fill_for_matching_editable_value() -> None:
    account = MailAccount("user@icloud.com", "", "", "", "raw")
    flow = OpenAIEmailRegisterFlow(account, "", True, None)
    email_input = Mock()
    email_input.input_value.return_value = account.email
    email_input.is_enabled.return_value = True
    email_input.is_editable.return_value = True
    flow._visible_inputs = Mock(return_value=[email_input])
    flow._click_continue = Mock(return_value=True)

    assert flow._fill_email_if_visible(Mock()) is True

    email_input.fill.assert_not_called()
    flow._click_continue.assert_called_once()
