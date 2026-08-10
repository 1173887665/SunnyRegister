from __future__ import annotations

from unittest.mock import Mock

import pytest

from sunny_core.mailbox import MailAccount
from sunny_core.openai_auth import (
    OpenAIEmailRegisterFlow,
    _country_option_score,
    _phone_country_context,
    _phone_number_candidates,
)
from sunny_core.worker import _sms_country_metadata


class FakeCollection:
    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])

    def count(self):
        return len(self.nodes)

    def nth(self, index):
        return self.nodes[index]


class FakeNode:
    def __init__(self, text="", attrs=None, options=None):
        self.text = text
        self.attrs = dict(attrs or {})
        self.options = list(options or [])
        self.clicked = False
        self.selected = ""

    def is_visible(self):
        return True

    def inner_text(self, **_kwargs):
        return self.text

    def get_attribute(self, name, **_kwargs):
        return self.attrs.get(name)

    def click(self, **_kwargs):
        self.clicked = True

    def locator(self, selector):
        return FakeCollection(self.options if selector == "option" else [])

    def select_option(self, *, value=None, label=None, **_kwargs):
        self.selected = str(value if value is not None else label)


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
    assert _phone_number_candidates("+601159137308", "60") == ["1159137308", "+601159137308", "601159137308"]


def test_us_country_keeps_default_selection() -> None:
    context = _phone_country_context({"country": "usa", "country_code": "1"}, "+12025550101")

    assert context["should_select"] is False
    assert _phone_number_candidates("+12025550101", "1")[0] == "2025550101"


def test_numeric_provider_country_id_is_not_treated_as_country_code() -> None:
    context = _phone_country_context({"country": "1"}, "+12025550101")

    assert context["hints"] == []
    assert context["should_select"] is False


def test_country_option_matches_dial_code_even_with_localized_name() -> None:
    context = _phone_country_context({"country": "mys", "country_code": "60"}, "+601159137308")

    score, dial = _country_option_score("マレーシア (+60)", ["MY"], context)

    assert score >= 130
    assert dial == "60"


def test_custom_country_picker_selects_matching_non_us_option() -> None:
    trigger = FakeNode("United States +1", {"aria-label": "Country code"})
    us_option = FakeNode("United States +1", {"data-value": "US"})
    malaysia_option = FakeNode("Malaysia +60", {"data-value": "MY"})
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
    native_select = FakeNode(options=[FakeNode("United States (+1)", {"value": "US"}), malaysia_option])
    page = FakePage({'select[name*="country" i]': [native_select]})
    flow = make_flow()

    dial = flow._select_phone_country(
        page,
        {"country_iso": "MY", "country_name": "Malaysia", "country_code": "60"},
        "+601159137308",
    )

    assert dial == "60"
    assert native_select.selected == "MY"


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
