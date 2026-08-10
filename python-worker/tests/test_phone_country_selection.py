from __future__ import annotations

import json
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from sunny_core.mailbox import MailAccount
from sunny_core.openai_auth import (
    OpenAIEmailRegisterFlow,
    _country_option_score,
    _e164_phone_number,
    _phone_country_context,
    _phone_number_candidates,
    _should_retry_phone_send_without_channel,
    _validate_phone_country_config,
)
from sunny_core.worker import _sms_country_metadata


class FakeCollection:
    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])

    def count(self):
        return len(self.nodes)

    def nth(self, index):
        return self.nodes[index]

    def locator(self, selector):
        nodes = []
        for node in self.nodes:
            nodes.extend(node.locator(selector).nodes)
        return FakeCollection(nodes)


class FakeNode:
    def __init__(self, text="", attrs=None, options=None, children=None, visible=True, on_click=None):
        self.text = text
        self.attrs = dict(attrs or {})
        self.options = list(options or [])
        self.children = dict(children or {})
        self.visible = visible
        self.on_click = on_click
        self.clicked = False
        self.selected = ""

    def is_visible(self):
        return self.visible

    def inner_text(self, **_kwargs):
        return self.text

    def get_attribute(self, name, **_kwargs):
        return self.attrs.get(name)

    def click(self, **_kwargs):
        self.clicked = True
        if self.on_click:
            self.on_click()

    def locator(self, selector):
        if selector == "option":
            return FakeCollection(self.options)
        if selector == "option:checked":
            return FakeCollection([option for option in self.options if option.attrs.get("value") == self.selected])
        return FakeCollection(self.children.get(selector, []))

    def select_option(self, *, value=None, label=None, **_kwargs):
        if value is not None and self.options and not any(option.attrs.get("value") == value for option in self.options):
            raise ValueError(f"unknown option: {value}")
        self.selected = str(value if value is not None else label)

    def input_value(self, **_kwargs):
        return self.selected


class FakePage:
    def __init__(self, selectors):
        self.selectors = selectors

    def locator(self, selector):
        return FakeCollection(self.selectors.get(selector, []))


def make_flow() -> OpenAIEmailRegisterFlow:
    account = MailAccount("user@example.com", "password", "client", "mail-rt", "raw")
    flow = OpenAIEmailRegisterFlow(account, "", True, None)
    flow._sleep_checked = Mock()
    return flow


def test_non_us_country_context_and_local_number_candidates() -> None:
    phone = {"country": "mys", "country_name": "Malaysia", "country_code": "60"}

    context = _phone_country_context(phone, "+601159137308")

    assert context["should_select"] is True
    assert context["dial_code"] == "60"
    assert context["country_iso"] == "MY"
    assert _phone_number_candidates("+601159137308", "60") == ["1159137308", "+601159137308", "601159137308"]


def test_configured_provider_country_overrides_conflicting_number_prefix() -> None:
    context = _phone_country_context(
        {"country": "usa", "country_name": "United States", "country_code": "1"},
        "+601159285992",
    )

    assert context["dial_code"] == "1"
    assert context["country_iso"] == "US"
    assert context["country_source"] == "provider"
    assert context["should_select"] is False


def test_provider_number_must_match_configured_country_code() -> None:
    with pytest.raises(RuntimeError, match=r"配置 \+1 不一致"):
        _validate_phone_country_config(
            {"country": "usa", "country_name": "United States", "country_code": "1"},
            "+819012345678",
        )

    context = _validate_phone_country_config(
        {"country": "jpn", "country_name": "Japan", "country_code": "81"},
        "+819012345678",
    )
    assert context["country_iso"] == "JP"


def test_china_prefix_resolves_standard_country_identity() -> None:
    context = _phone_country_context({}, "+8613812345678")

    assert context["dial_code"] == "86"
    assert context["country_iso"] == "CN"
    assert context["should_select"] is True


def test_e164_normalization_keeps_full_international_number() -> None:
    assert _e164_phone_number("+60 11-3798-4883") == "+601137984883"
    assert _e164_phone_number("0060137984883") == "+60137984883"

    with pytest.raises(RuntimeError, match="E.164"):
        _e164_phone_number("123")


def test_phone_send_retry_matches_reference_project_conditions() -> None:
    assert _should_retry_phone_send_without_channel({"status": 400, "text": "unexpected channel field"}) is True
    assert _should_retry_phone_send_without_channel({"status": 409, "text": "invalid_state session"}) is True
    assert _should_retry_phone_send_without_channel({"status": 500, "text": "session"}) is False


def test_us_country_keeps_default_selection() -> None:
    context = _phone_country_context({"country": "usa", "country_code": "1"}, "+12025550101")

    assert context["should_select"] is False
    assert _phone_number_candidates("+12025550101", "1")[0] == "2025550101"


def test_us_config_never_opens_country_picker() -> None:
    trigger = FakeNode("Japan +81", {"aria-label": "Country code"})
    page = FakePage({'button[role="combobox"]': [trigger]})
    flow = make_flow()

    dial = flow._select_phone_country(
        page,
        {"country": "usa", "country_name": "United States", "country_code": "1"},
        "+12025550101",
    )

    assert dial == "1"
    assert trigger.clicked is False


def test_numeric_provider_country_id_is_not_treated_as_country_code() -> None:
    context = _phone_country_context({"country": "1"}, "+12025550101")

    assert "1" not in context["hints"]
    assert context["country_iso"] == "US"
    assert context["should_select"] is False


def test_country_option_matches_dial_code_even_with_localized_name() -> None:
    context = _phone_country_context({"country": "mys", "country_code": "60"}, "+601159137308")

    score, dial = _country_option_score("マレーシア (+60)", ["MY"], context)

    assert score >= 130
    assert dial == "60"


def test_custom_country_picker_selects_matching_non_us_option() -> None:
    trigger = FakeNode("United States +1", {"aria-label": "Country code"})
    us_option = FakeNode("United States +1", {"data-value": "US"})
    malaysia_option = FakeNode(
        "Malaysia +60",
        {"data-value": "MY"},
        on_click=lambda: trigger.attrs.update({"data-value": "MY", "aria-label": "Malaysia +60"}),
    )
    page = FakePage({
        'button[role="combobox"]': [trigger],
        '[role="option"]': [us_option, malaysia_option],
    })
    flow = make_flow()

    dial = flow._select_phone_country(
        page,
        {"country": "mys", "country_name": "Malaysia", "country_code": "60"},
        "+601159137308",
    )

    assert dial == "60"
    assert trigger.clicked is True
    assert malaysia_option.clicked is True
    assert us_option.clicked is False


def test_native_country_select_uses_configured_country() -> None:
    malaysia_option = FakeNode("Malaysia (+60)", {"value": "MY"})
    native_select = FakeNode(
        attrs={"aria-label": "Phone number country"},
        options=[FakeNode("United States (+1)", {"value": "US"}), malaysia_option],
        visible=False,
    )
    page = FakePage({'select[aria-label*="country" i]': [native_select]})
    flow = make_flow()

    dial = flow._select_phone_country(
        page,
        {"country_iso": "MY", "country_name": "Malaysia", "country_code": "60"},
        "+601159137308",
    )

    assert dial == "60"
    assert native_select.selected == "MY"


def test_native_country_select_falls_back_to_dial_code_for_nonstandard_value() -> None:
    native_select = FakeNode(
        attrs={"aria-label": "Phone number country"},
        options=[FakeNode("United States (+1)", {"value": "usa"}), FakeNode("Malaysia (+60)", {"value": "mys"})],
        visible=False,
    )
    page = FakePage({'select[aria-label*="country" i]': [native_select]})
    flow = make_flow()

    dial = flow._select_phone_country(page, {}, "+601159285992")

    assert dial == "60"
    assert native_select.selected == "mys"


def test_japan_config_selects_japan_instead_of_us() -> None:
    native_select = FakeNode(
        attrs={"aria-label": "Phone number country"},
        options=[FakeNode("United States (+1)", {"value": "US"}), FakeNode("Japan (+81)", {"value": "JP"})],
        visible=False,
    )
    page = FakePage({'select[aria-label*="country" i]': [native_select]})
    flow = make_flow()

    dial = flow._select_phone_country(
        page,
        {"country": "jpn", "country_name": "Japan", "country_code": "81"},
        "+819012345678",
    )

    assert dial == "81"
    assert native_select.selected == "JP"


def test_country_picker_uses_aria_controlled_listbox() -> None:
    trigger = FakeNode("+1", {"aria-label": "Country code", "aria-controls": "phone-country-list"})
    malaysia_option = FakeNode(
        "Malaysia +60",
        {"data-value": "MY"},
        on_click=lambda: trigger.attrs.update({"data-value": "MY", "aria-label": "Malaysia +60"}),
    )
    listbox = FakeNode(children={'[role="option"]': [malaysia_option]})
    page = FakePage({
        'button[role="combobox"]': [trigger],
        '[id="phone-country-list"]': [listbox],
    })
    flow = make_flow()

    dial = flow._select_phone_country(page, {}, "+601159285992")

    assert dial == "60"
    assert malaysia_option.clicked is True


def test_phone_api_submits_full_e164_number_and_returns_verification_url() -> None:
    page = Mock()
    page.url = "https://auth.openai.com/add-phone"
    flow = make_flow()
    response = {
        "ok": True,
        "status": 200,
        "text": '{"continue_url":"/phone-verification"}',
        "data": {"continue_url": "/phone-verification"},
    }

    with patch("sunny_core.openai_auth.browser_fetch", return_value=response) as fetch:
        continue_url = flow._send_phone_code_api(page, "+601137984883")

    assert continue_url == "https://auth.openai.com/phone-verification"
    request = fetch.call_args
    assert request.args[1] == "https://auth.openai.com/api/accounts/add-phone/send"
    assert json.loads(request.kwargs["body"]) == {"phone_number": "+601137984883", "channel": "sms"}
    assert request.kwargs["headers"]["x-access-flow-invocation-id"]
    assert request.kwargs["headers"]["oai-device-id"]


def test_phone_api_retries_without_channel_like_reference_project() -> None:
    page = Mock()
    page.url = "https://auth.openai.com/add-phone"
    flow = make_flow()
    rejected = {"ok": False, "status": 400, "text": '{"error":{"message":"unexpected channel field"}}'}
    accepted = {
        "ok": True,
        "status": 200,
        "text": "{}",
        "data": {"page": {"payload": {"url": "https://auth.openai.com/phone-verification"}}},
    }

    with patch("sunny_core.openai_auth.browser_fetch", side_effect=[rejected, accepted]) as fetch:
        continue_url = flow._send_phone_code_api(page, "+601111314592")

    assert continue_url == "https://auth.openai.com/phone-verification"
    assert fetch.call_count == 2
    assert json.loads(fetch.call_args_list[0].kwargs["body"])["channel"] == "sms"
    assert json.loads(fetch.call_args_list[1].kwargs["body"]) == {"phone_number": "+601111314592"}


def test_phone_api_failure_returns_none_to_the_selected_mode_strategy() -> None:
    page = Mock()
    page.url = "https://auth.openai.com/add-phone"
    flow = make_flow()

    with patch(
        "sunny_core.openai_auth.browser_fetch",
        return_value={"ok": False, "status": 403, "text": "request rejected"},
    ):
        assert flow._send_phone_code_api(page, "+60106539484") is None

    with patch(
        "sunny_core.openai_auth.browser_fetch",
        return_value={"ok": True, "status": 200, "text": "<html>route error</html>", "data": None},
    ):
        assert flow._send_phone_code_api(page, "+60106539484") is None


@pytest.mark.parametrize("execution_mode", ["protocol", "protocol_post_stage", "protocol_headless_fallback"])
def test_protocol_execution_uses_e164_submission_without_country_picker(execution_mode: str) -> None:
    flow = make_flow()
    flow.execution_mode = execution_mode
    flow._send_phone_code_api = Mock(return_value="https://auth.openai.com/phone-verification")
    flow._select_phone_country = Mock(side_effect=AssertionError("country picker must not run"))

    verification_url, selected_dial = flow._prepare_phone_submission(
        Mock(),
        {"country": "mys", "country_code": "60"},
        "+601137984883",
    )

    assert verification_url == "https://auth.openai.com/phone-verification"
    assert selected_dial == ""
    flow._send_phone_code_api.assert_called_once()
    flow._select_phone_country.assert_not_called()


def test_background_execution_uses_country_picker_before_protocol_fallback() -> None:
    flow = make_flow()
    flow.execution_mode = "background"
    flow._select_phone_country = Mock(return_value="60")
    flow._send_phone_code_api = Mock(side_effect=AssertionError("protocol fallback must not run"))

    verification_url, selected_dial = flow._prepare_phone_submission(
        Mock(),
        {"country": "mys", "country_code": "60"},
        "+601137984883",
    )

    assert verification_url is None
    assert selected_dial == "60"
    flow._select_phone_country.assert_called_once()
    flow._send_phone_code_api.assert_not_called()


def test_background_country_failure_degrades_to_e164_submission() -> None:
    flow = make_flow()
    flow.execution_mode = "background"
    flow._select_phone_country = Mock(side_effect=RuntimeError("country control unavailable"))
    flow._send_phone_code_api = Mock(return_value="https://auth.openai.com/phone-verification")

    verification_url, selected_dial = flow._prepare_phone_submission(
        Mock(),
        {"country": "mys", "country_code": "60"},
        "+601137984883",
    )

    assert verification_url == "https://auth.openai.com/phone-verification"
    assert selected_dial == ""
    flow._send_phone_code_api.assert_called_once()


def test_visible_country_failure_does_not_silently_use_protocol_submission() -> None:
    flow = make_flow()
    flow.execution_mode = "visible"
    flow._select_phone_country = Mock(side_effect=RuntimeError("country control unavailable"))
    flow._send_phone_code_api = Mock()

    with pytest.raises(RuntimeError, match="country control unavailable"):
        flow._prepare_phone_submission(
            Mock(),
            {"country": "mys", "country_code": "60"},
            "+601137984883",
        )

    flow._send_phone_code_api.assert_not_called()


def test_non_us_country_fails_instead_of_submitting_under_us() -> None:
    flow = make_flow()
    page = FakePage({})

    with pytest.raises(RuntimeError, match="无法在手机号页面选择国家"):
        flow._select_phone_country(page, {"country": "mys", "country_code": "60"}, "+601159137308")


def test_firefox_country_metadata_uses_configured_country_details() -> None:
    db = Mock()
    db.sms_provider_option_extra.return_value = {
        "Country_ID": "mys",
        "Country_Area": "60",
        "Country_Title": "+60/马来西亚/malaysia",
    }

    metadata = _sms_country_metadata(db, {"label": "Malaysia (+60)"}, "mys")

    assert metadata == {
        "country": "mys",
        "country_iso": "mys",
        "country_name": "malaysia",
        "country_code": "60",
    }
