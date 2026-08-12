from __future__ import annotations

import base64
from unittest.mock import Mock, patch

import pytest

from sunny_core.auth_challenges import generate_totp, normalize_totp_secret
from sunny_core.luban_sms import LubanSMSClient, LubanSMSError
from sunny_core.mailbox import MailAccount, URLAPIICloudReader, account_from_row
from sunny_core.openai_auth import OpenAIEmailRegisterFlow
from sunny_core.otp_candidates import extract_otp_candidates
from sunny_core.protocol_auth import ProtocolRegistrationFlow


def test_totp_matches_rfc_vector_and_rejects_invalid_base32() -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert generate_totp(secret, timestamp=59) == "287082"
    assert normalize_totp_secret("jbsw y3dp ehpk3pxp====") == "JBSWY3DPEHPK3PXP"
    with pytest.raises(ValueError, match="Base32"):
        normalize_totp_secret("not-a-valid-secret")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"data":{"verification_code":123456}}', "123456"),
        ("<html><body>ChatGPT verification code: <b>234567</b></body></html>", "234567"),
        ("<html><body>OpenAI code <span>2</span><span>4</span><span>6</span><span>8</span><span>0</span><span>2</span></body></html>", "246802"),
        ('<script type="application/json">{"mail":{"body":"OpenAI verification code: 5 6 7 8 9 0"}}</script>', "567890"),
        ('<script>window.__MAIL__={"body":"ChatGPT code: \\u0031\\u0033\\u0035\\u0037\\u0039\\u0031"}</script>', "135791"),
        ("Your OpenAI one-time code is 345678", "345678"),
        (base64.b64encode(b"ChatGPT OTP: 456789").decode(), "456789"),
    ],
)
def test_extract_otp_candidates_supports_common_payloads(payload: str, expected: str) -> None:
    candidates = extract_otp_candidates(payload)
    assert candidates and candidates[0]["code"] == expected
    assert not extract_otp_candidates("ChatGPT reference 1234567 and order 12345")


def test_url_api_html_prefers_mail_body_code_over_sender_suffix_and_date() -> None:
    payload = """
    <section>
      <summary>
        <span class="subject">ChatGPT 用の一時ログインコード</span>
        <span class="date">2026-08-12 20:02:39</span>
      </summary>
      <div class="meta">发件人：noreply_at_tm_openai_com_xd721508@icloud.com</div>
      <div class="body body-rich">この一時検証コードを入力して続行してください:\n536587\n検証コードをリクエストしていない場合、このメールは無視してください。</div>
    </section>
    """

    candidates = extract_otp_candidates(payload)

    assert candidates[0]["code"] == "536587"
    assert candidates[0]["score"] >= 80
    scores = {item["code"]: item["score"] for item in candidates}
    assert scores["721508"] < 0
    assert scores["202608"] < 0


def test_url_api_candidate_key_changes_for_same_code_in_new_mail() -> None:
    first = '<span class="subject">ChatGPT code</span><span class="date">2026-08-12 20:02:39</span><div class="body">Verification code: 536587</div>'
    second = '<span class="subject">ChatGPT code</span><span class="date">2026-08-12 20:05:10</span><div class="body">Verification code: 536587</div>'

    first_candidate = extract_otp_candidates(first)[0]
    second_candidate = extract_otp_candidates(second)[0]

    assert first_candidate["code"] == second_candidate["code"] == "536587"
    assert first_candidate["key"] != second_candidate["key"]


def test_url_api_reader_ignores_baseline_and_returns_new_code() -> None:
    account = MailAccount(
        email="user@icloud.com",
        password="",
        client_id="",
        refresh_token="",
        raw="user@icloud.com----https://mail.example.test/latest",
        mailbox_type="apple",
        mailbox_channel="url_api",
        access_key="https://mail.example.test/latest",
    )
    reader = URLAPIICloudReader.__new__(URLAPIICloudReader)
    reader.account = account
    reader.log = lambda _message: None
    reader.seen_candidate_keys = set()
    reader.candidate_counts = {}
    old = extract_otp_candidates("ChatGPT verification code 111111")
    new = extract_otp_candidates("ChatGPT verification code 222222")
    responses = [
        {"otp_candidates": old},
        {"otp_candidates": old},
        {"otp_candidates": new + old},
    ]
    reader._latest = Mock(side_effect=responses)

    reader.connect()
    with patch("sunny_core.mailbox.time.sleep", return_value=None):
        assert reader.wait_for_code(0, timeout=5) == "222222"


def test_url_api_reader_never_returns_low_confidence_sender_number() -> None:
    account = MailAccount(
        email="user@icloud.com",
        password="",
        client_id="",
        refresh_token="",
        raw="user@icloud.com----https://mail.example.test/latest",
        mailbox_type="apple",
        mailbox_channel="url_api",
        access_key="https://mail.example.test/latest",
    )
    reader = URLAPIICloudReader.__new__(URLAPIICloudReader)
    reader.account = account
    reader.log = lambda _message: None
    reader.seen_candidate_keys = set()
    reader.candidate_counts = {}
    noise = {"code": "721508", "key": "sender-key", "score": -160}
    reader._latest = Mock(return_value={"otp_candidates": [noise]})

    with patch("sunny_core.mailbox.time.sleep", return_value=None):
        with pytest.raises(TimeoutError):
            reader.wait_for_code(0, timeout=0.01)


def test_url_api_account_row_distinguishes_password_from_mail_url() -> None:
    password_only = account_from_row({
        "email": "user@icloud.com",
        "mailbox_type": "apple",
        "mailbox_channel": "url_api",
        "raw": "user@icloud.com----chatgpt-password",
    })
    assert password_only.chatgpt_password == "chatgpt-password"
    assert password_only.access_key == ""

    with_url = account_from_row({
        "mailbox_type": "apple",
        "mailbox_channel": "url_api",
        "raw": "user@icloud.com----chatgpt-password----https://mail.example.test/latest----JBSWY3DPEHPK3PXP",
    })
    assert with_url.access_key == "https://mail.example.test/latest"
    assert with_url.totp_secret == "JBSWY3DPEHPK3PXP"


def test_luban_sms_lifecycle_and_error_classification() -> None:
    client = LubanSMSClient({"luban_api_key": "key", "luban_service_id": "openai", "luban_base_url": "https://sms.example.test"})
    client._request = Mock(
        side_effect=[
            {"code": 0, "request_id": "req-1", "number": "12025550123"},
            {"code": 0, "msg": "wait"},
            {"code": 0, "sms_msg": "Your ChatGPT code is 654321"},
            {"code": 0},
        ]
    )
    activation = client.get_number()
    assert activation.number == "+12025550123"
    with patch("sunny_core.luban_sms.time.sleep", return_value=None):
        assert client.wait_code(activation.request_id, timeout=10) == "654321"
    client.release(activation.request_id)
    assert client._request.call_args_list[-1].args == ("setStatus",)

    terminal = client._error({"code": 401, "msg": "bad key"}, "failed")
    assert isinstance(terminal, LubanSMSError) and terminal.terminal is True


def test_luban_sms_extracts_nested_code() -> None:
    client = LubanSMSClient({"luban_api_key": "key", "luban_service_id": "openai"})
    client._request = Mock(return_value={"code": 0, "data": {"message": "Your OpenAI code is 654321"}})
    assert client.wait_code("request-1", timeout=1) == "654321"


def test_browser_password_login_uses_exact_imported_password() -> None:
    account = MailAccount("user@example.com", "mailbox-password", "client", "mail-rt", "raw", chatgpt_password="Short1!")
    flow = OpenAIEmailRegisterFlow(account, "", True, None, existing_account=True)
    password_input = Mock()
    flow._visible_inputs = Mock(return_value=[password_input])
    flow._click_continue = Mock(return_value=True)
    page = Mock(url="https://auth.openai.com/log-in/password")

    flow._fill_password_step(page)

    password_input.fill.assert_called_once_with("Short1!")
    assert account.chatgpt_password == "Short1!"


def test_browser_can_switch_password_page_to_email_otp() -> None:
    account = MailAccount("user@icloud.com", "", "", "", "raw", mailbox_type="apple", mailbox_channel="url_api", access_key="https://mail.example.test")
    flow = OpenAIEmailRegisterFlow(account, "", True, None, existing_account=True)
    target = Mock()
    target.is_visible.return_value = True
    locator = Mock()
    locator.first = target
    page = Mock()
    page.locator.return_value = locator

    assert flow._switch_password_to_email_code(page) is True
    target.click.assert_called_once()


def test_protocol_totp_and_workspace_challenges_use_first_available_workspace() -> None:
    account = MailAccount("user@example.com", "", "", "", "raw", totp_secret="JBSWY3DPEHPK3PXP")
    flow = ProtocolRegistrationFlow(account)
    mfa_result = {"page": {"type": "workspace"}, "oai-client-auth-session": {"workspaces": [{"id": "personal"}, {"id": "team"}]}}
    flow._auth_json_post = Mock(side_effect=[{}, mfa_result, {"continue_url": "https://chatgpt.com/callback"}])
    challenge = {
        "page": {"type": "mfa_challenge"},
        "continue_url": "https://auth.openai.com/mfa-challenge/factor",
        "oai-client-auth-session": {"mfa_challenge_factors": [{"factor_type": "totp", "id": "factor"}]},
    }

    after_mfa = flow._complete_mfa(challenge)
    selected = flow._select_workspace(after_mfa)

    assert selected["continue_url"].endswith("/callback")
    calls = flow._auth_json_post.call_args_list
    assert calls[1].args[0] == "/api/accounts/mfa/verify"
    assert len(calls[1].args[1]["code"]) == 6
    assert calls[2].args[1] == {"workspace_id": "personal"}
