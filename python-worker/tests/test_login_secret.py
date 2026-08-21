import unittest
import time
import json
from unittest.mock import Mock, patch

from sunny_core.login_secret import RECENT_EMAIL_CODE_MAX_AGE_SECONDS, LoginSecretSetupFlow, ProtocolLoginSecretSetupFlow, _invalid_auth_state, _password_already_set, _wrong_email_otp, generate_chatgpt_password
from sunny_core.protocol_auth import ProtocolChallengeRequired
from sunny_core.mailbox import MailAccount, extract_otp


class LoginSecretTests(unittest.TestCase):
    def test_mailbox_otp_requires_six_digits(self):
        self.assertEqual(extract_otp("Your OpenAI code is 123456"), "123456")
        self.assertEqual(extract_otp("Your OpenAI code is 1234"), "")
        self.assertEqual(extract_otp("Reference 12345; code 1234567"), "")

    def test_password_already_set_response_is_detected_without_accepting_unknown_password(self):
        self.assertTrue(_password_already_set({"status": 400, "data": {"code": "password_already_set"}}))
        self.assertTrue(_password_already_set({"status": 400, "data": {"message": "You already have a password."}}))
        self.assertTrue(_password_already_set({"status": 409, "data": {"error": {"type": "password_exists"}}}))
        self.assertFalse(_password_already_set({"status": 400, "data": {"code": "invalid_request_error"}}))

    def test_wrong_email_otp_response_is_recognized_for_retry(self):
        self.assertTrue(_wrong_email_otp({"data": {"code": "wrong_email_otp_code"}}))
        self.assertTrue(_wrong_email_otp({"data": {"message": "Wrong code. Please check it."}}))
        self.assertFalse(_wrong_email_otp({"data": {"code": "account_deactivated"}}))

    def test_invalid_auth_state_is_distinguished_from_generic_conflict(self):
        self.assertTrue(_invalid_auth_state({"data": {"error": {"code": "invalid_state"}}}))
        self.assertTrue(_invalid_auth_state(None, "Your sign-in session is no longer valid"))
        self.assertFalse(_invalid_auth_state({"data": {"error": {"code": "conflict"}}}))

    def test_browser_reauthentication_timeout_resends_and_waits_again(self):
        class Reader:
            def __init__(self):
                self.calls = []

            def wait_for_code(self, timestamp, timeout):
                self.calls.append((timestamp, timeout))
                if len(self.calls) == 1:
                    raise TimeoutError("mailbox timeout")
                return "654321"

        class Page:
            def __init__(self):
                self.url = ""
                self.codes = []

            def goto(self, url, **_kwargs):
                self.url = url

            def evaluate(self, script, code=None):
                if "email-otp/validate" in script:
                    self.codes.append(code)
                    return {"ok": True, "status": 200, "data": {"continue_url": "https://chatgpt.com/api/auth/callback/openai"}}
                if "api/auth/session" in script:
                    return {"ok": True, "status": 200, "data": {"accessToken": "access-token"}}
                return False

        flow = LoginSecretSetupFlow(self._account(), {}, "")
        flow.reader = Reader()
        page = Page()
        with patch.object(flow, "_click_resend_email_code", return_value=True) as resend:
            result = flow._reauthenticate_with_fresh_email_code(
                page, "https://auth.openai.com/authorize", time.time()
            )
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual(page.codes, ["654321"])
        self.assertEqual([timeout for _timestamp, timeout in flow.reader.calls], [120, 60])
        resend.assert_called_once_with(page)

    def test_password_reauthentication_tries_recent_registration_code_before_mailbox(self):
        class Reader:
            def wait_for_code(self, *_args):
                raise AssertionError("the recent registration code should be tried first")

        class Page:
            def __init__(self):
                self.url = ""
                self.codes = []

            def goto(self, url, **_kwargs):
                self.url = url

            def evaluate(self, script, code=None):
                if "email-otp/validate" in script:
                    self.codes.append(code)
                    return {"ok": True, "status": 200, "data": {"continue_url": "https://chatgpt.com/api/auth/callback/openai"}}
                if "api/auth/session" in script:
                    return {"ok": True, "status": 200, "data": {"accessToken": "access-token"}}
                return False

        flow = LoginSecretSetupFlow(self._account(), {}, "")
        flow.reader = Reader()
        page = Page()
        result = flow._reauthenticate_with_fresh_email_code(
            page,
            "https://auth.openai.com/authorize",
            time.time(),
            recent_email_code="123456",
            recent_email_code_at=time.time(),
            prefer_recent_email_code=True,
        )
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual(page.codes, ["123456"])

    def test_browser_login_secret_refreshes_access_token_after_security_change(self):
        class Context:
            def storage_state(self):
                return {"cookies": [{"name": "session", "value": "new"}]}

        class Flow(LoginSecretSetupFlow):
            def __init__(self, account):
                super().__init__(account, {"access_token": "old-token", "expires_at": 1}, "")
                self.session_reads = 0

            def _session_json(self, _page):
                self.session_reads += 1
                return {"accessToken": "old-token" if self.session_reads == 1 else "new-token"}

            def _add_password(self, _page):
                return "new-password"

            def _refresh_session_with_login_secret(self, _page):
                self.session_reads += 1
                return {"accessToken": "new-token"}

        account = self._account()
        account.chatgpt_password = ""
        account.totp_secret = "JBSWY3DPEHPK3PXP"
        flow = Flow(account)
        result = flow._run_on_page(Mock(), Context())
        self.assertEqual(result["session"]["access_token"], "new-token")
        self.assertEqual(result["session"]["session_json"]["accessToken"], "new-token")
        self.assertTrue(result["access_token_refreshed"])
        self.assertNotIn("expires_at", result["session"])
        self.assertEqual(flow.session_reads, 2)

    def test_protocol_login_secret_refreshes_access_token_after_security_change(self):
        class Flow(ProtocolLoginSecretSetupFlow):
            def __init__(self, account):
                protocol_session = Mock()
                protocol_session.refresh_session_with_login_secret.return_value = {
                    "session_json": {"accessToken": "new-token"}
                }
                super().__init__(account, {"access_token": "old-token", "expires_at": 1}, protocol_session)
                self.session_reads = 0

            def _session_json(self):
                self.session_reads += 1
                return {"accessToken": "old-token" if self.session_reads == 1 else "new-token"}

            def _add_password(self, _password):
                return {"accessToken": "after-password"}

        account = self._account()
        account.chatgpt_password = ""
        account.totp_secret = "JBSWY3DPEHPK3PXP"
        result = Flow(account).run()
        self.assertEqual(result["session"]["access_token"], "new-token")
        self.assertEqual(result["session"]["session_json"]["accessToken"], "new-token")
        self.assertTrue(result["access_token_refreshed"])
        self.assertNotIn("expires_at", result["session"])

    def test_login_secret_is_incomplete_when_reauthentication_returns_old_access_token(self):
        class Flow(LoginSecretSetupFlow):
            def __init__(self, account):
                super().__init__(account, {"access_token": "old-token"}, "")

            def _session_json(self, _page):
                return {"accessToken": "old-token"}

            def _add_password(self, _page):
                return "new-password"

            def _refresh_session_with_login_secret(self, _page):
                raise RuntimeError("登录密钥重认证后仍返回注册阶段的旧 Access Token")

        account = self._account()
        account.chatgpt_password = ""
        account.totp_secret = "JBSWY3DPEHPK3PXP"
        result = Flow(account)._run_on_page(Mock(), Mock(storage_state=lambda: {"cookies": []}))
        self.assertFalse(result["complete"])
        self.assertFalse(result["access_token_refreshed"])
        self.assertTrue(any("旧 Access Token" in error for error in result["errors"]))

    def test_password_reauthentication_reads_distinct_new_code_after_recent_code_rejected(self):
        class Reader:
            def __init__(self):
                self.calls = []

            def wait_for_code(self, _timestamp, timeout):
                self.calls.append(timeout)
                return "654321"

        class Page:
            def __init__(self):
                self.url = ""
                self.codes = []

            def goto(self, url, **_kwargs):
                self.url = url

            def evaluate(self, script, code=None):
                if "email-otp/validate" in script:
                    self.codes.append(code)
                    if len(self.codes) == 1:
                        return {"ok": False, "status": 401, "data": {"code": "wrong_email_otp_code", "message": "Wrong code"}}
                    return {"ok": True, "status": 200, "data": {"continue_url": "https://chatgpt.com/api/auth/callback/openai"}}
                if "api/auth/session" in script:
                    return {"ok": True, "status": 200, "data": {"accessToken": "access-token"}}
                return False

        flow = LoginSecretSetupFlow(self._account(), {}, "")
        flow.reader = Reader()
        page = Page()
        result = flow._reauthenticate_with_fresh_email_code(
            page,
            "https://auth.openai.com/authorize",
            time.time(),
            recent_email_code="123456",
            recent_email_code_at=time.time(),
            prefer_recent_email_code=True,
        )
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual(page.codes, ["123456", "654321"])
        self.assertEqual(flow.reader.calls, [10])

    def test_protocol_reauthentication_timeout_resends_once(self):
        class Reader:
            def __init__(self):
                self.calls = []

            def wait_for_code(self, timestamp, timeout):
                self.calls.append((timestamp, timeout))
                if len(self.calls) == 1:
                    raise TimeoutError("mailbox timeout")
                return "654321"

        class Flow(ProtocolLoginSecretSetupFlow):
            def __init__(self, account):
                super().__init__(account, {}, object())
                self.reader = Reader()
                self.requests = []

            def _request(self, method, url, **kwargs):
                self.requests.append((method, url, kwargs))
                if url.endswith("/api/auth/csrf"):
                    return 200, {"csrfToken": "csrf-token"}, ""
                if "/api/auth/signin/openai?" in url:
                    return 200, {"url": "https://auth.openai.com/authorize"}, ""
                if url.endswith("/api/accounts/email-otp/validate"):
                    return 200, {"continue_url": "https://chatgpt.com/api/auth/callback/openai"}, ""
                if url.endswith("/api/auth/session"):
                    return 200, {"accessToken": "access-token"}, ""
                return 200, {}, ""

        flow = Flow(self._account())
        result = flow._reauthenticate("https://chatgpt.com/?action=add_password")
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual([timeout for _timestamp, timeout in flow.reader.calls], [120, 60])
        resend_requests = [item for item in flow.requests if item[1].endswith("/api/accounts/email-otp/send")]
        self.assertEqual(len(resend_requests), 1)
        self.assertEqual(resend_requests[0][2]["headers"]["referer"], "https://auth.openai.com/authorize")

    def test_protocol_password_reauthentication_tries_recent_code_then_distinct_code(self):
        class Reader:
            def __init__(self):
                self.calls = []

            def wait_for_code(self, _timestamp, timeout):
                self.calls.append(timeout)
                return "654321"

        class Flow(ProtocolLoginSecretSetupFlow):
            def __init__(self, account):
                super().__init__(
                    account,
                    {},
                    object(),
                    recent_email_code="123456",
                    recent_email_code_at=time.time(),
                )
                self.reader = Reader()
                self.codes = []

            def _request(self, method, url, **kwargs):
                if url.endswith("/api/auth/csrf"):
                    return 200, {"csrfToken": "csrf-token"}, ""
                if "/api/auth/signin/openai?" in url:
                    return 200, {"url": "https://auth.openai.com/authorize"}, ""
                if url.endswith("/api/accounts/email-otp/validate"):
                    code = kwargs.get("json", {}).get("code")
                    self.codes.append(code)
                    if len(self.codes) == 1:
                        return 401, {"code": "wrong_email_otp_code", "message": "Wrong code"}, "Wrong code"
                    return 200, {"continue_url": "https://chatgpt.com/api/auth/callback/openai"}, ""
                if url.endswith("/api/auth/session"):
                    return 200, {"accessToken": "access-token"}, ""
                return 200, {}, ""

        flow = Flow(self._account())
        result = flow._reauthenticate(
            "https://chatgpt.com/?action=add_password",
            prefer_recent_email_code=True,
        )
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual(flow.codes, ["123456", "654321"])
        self.assertEqual(flow.reader.calls, [10])

    def test_protocol_password_invalid_state_reauthenticates_before_retry(self):
        flow = ProtocolLoginSecretSetupFlow(self._account(), {}, object())
        flow._reauthenticate = Mock(return_value={"accessToken": "access-token"})
        flow._request = Mock(side_effect=[
            (409, {"error": {"code": "invalid_state", "message": "Your sign-in session is no longer valid."}}, "invalid_state"),
            (200, {"ok": True}, ""),
        ])
        flow._session_json = Mock(return_value={"accessToken": "new-access-token"})

        result = flow._add_password("Strong-password-1!")

        self.assertEqual(result["accessToken"], "new-access-token")
        self.assertEqual(flow._reauthenticate.call_count, 2)

    def test_protocol_challenge_is_exposed_for_native_browser_takeover(self):
        account = self._account()
        account.chatgpt_password = ""
        account.totp_secret = "JBSWY3DPEHPK3PXP"
        flow = ProtocolLoginSecretSetupFlow(account, {"access_token": "old-token"}, object())
        flow._session_json = Mock(return_value={"accessToken": "old-token"})
        flow._add_password = Mock(side_effect=ProtocolChallengeRequired("Sentinel challenge"))

        result = flow.run()

        self.assertTrue(result["browser_challenge_required"])
        self.assertFalse(result["complete"])

    def test_browser_takeover_can_force_at_refresh_with_complete_login_secret(self):
        class Context:
            @staticmethod
            def storage_state():
                return {"cookies": [{"name": "session", "value": "new"}]}

        class Flow(LoginSecretSetupFlow):
            def _ensure_chatgpt_page(self, _page):
                return None

            def _dismiss_continue_gate(self, _page):
                return False

            def _session_json(self, _page):
                return {"accessToken": "old-token"}

            def _refresh_session_with_login_secret(self, _page):
                return {"accessToken": "new-token"}

        account = self._account()
        account.chatgpt_password = "ChatGPT-password"
        account.totp_secret = "JBSWY3DPEHPK3PXP"
        result = Flow(
            account,
            {"access_token": "old-token", "expires_at": 1},
            "",
            force_access_token_refresh=True,
        )._run_on_page(Mock(), Context())

        self.assertTrue(result["complete"])
        self.assertTrue(result["access_token_refreshed"])
        self.assertEqual(result["session"]["access_token"], "new-token")
        self.assertNotIn("expires_at", result["session"])

    @staticmethod
    def _account():
        return MailAccount(
            email="user@example.com",
            password="mail-password",
            client_id="client-id",
            refresh_token="refresh-token",
            raw="user@example.com----mail-password----client-id----refresh-token",
            chatgpt_password="ChatGPT-password",
        )

    def test_generated_password_has_required_length_and_character_classes(self):
        password = generate_chatgpt_password(20)
        self.assertEqual(len(password), 20)
        self.assertRegex(password, r"[A-Z]")
        self.assertRegex(password, r"[a-z]")
        self.assertRegex(password, r"[0-9]")
        self.assertRegex(password, r"[!@#$%^&*?_\-+=]")

    def test_complete_credentials_are_skipped_without_browser(self):
        account = MailAccount(
            email="user@example.com",
            password="mail-password",
            client_id="client-id",
            refresh_token="refresh-token",
            raw="user@example.com----mail-password----client-id----refresh-token",
            chatgpt_password="ChatGPT-password",
            totp_secret="JBSWY3DPEHPK3PXP",
        )
        result = LoginSecretSetupFlow(account, {}, "").run()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["password"], "ChatGPT-password")
        self.assertEqual(result["totp_secret"], "JBSWY3DPEHPK3PXP")

    def test_registration_browser_context_is_reused_for_login_secret(self):
        account = self._account()
        account.chatgpt_password = ""
        account.totp_secret = ""
        flow = LoginSecretSetupFlow(account, {"storage_state_json": {"cookies": []}}, "")
        page = object()
        context = object()
        expected = {"complete": False, "errors": ["stub"]}
        with patch.object(flow, "_run_on_page", return_value=expected) as run_on_page:
            with patch("sunny_core.login_secret.open_registration_browser", side_effect=AssertionError("unexpected second browser")):
                result = flow.run(browser_page=page, browser_context=context)
        self.assertIs(result, expected)
        run_on_page.assert_called_once_with(page, context)

    def test_protocol_login_secret_skips_complete_credentials_without_network(self):
        account = self._account()
        account.totp_secret = "JBSWY3DPEHPK3PXP"
        flow = ProtocolLoginSecretSetupFlow(account, {}, object())
        result = flow.run()
        self.assertTrue(result["skipped"])
        self.assertTrue(result["complete"])

    def test_password_protocol_endpoint_uses_existing_auth_state(self):
        class FakePage:
            def __init__(self):
                self.url = ""
                self.visited = []

            def goto(self, url, **_kwargs):
                self.visited.append(url)
                self.url = url

            def evaluate(self, _script, password):
                self.password = password
                return {"ok": True, "status": 200, "data": {"success": True}}

        page = FakePage()
        result = LoginSecretSetupFlow._add_password_via_protocol(page, "Strong-password-1!")
        self.assertTrue(result["ok"])
        self.assertEqual(page.visited, ["https://auth.openai.com/reset-password/new-password"])
        self.assertEqual(page.password, "Strong-password-1!")

    def test_settings_surface_opens_profile_menu_before_searching_settings(self):
        class FakePage:
            def __init__(self):
                self.script = ""

            def evaluate(self, script):
                self.script = script
                return True

        page = FakePage()
        self.assertTrue(LoginSecretSetupFlow._open_settings_surface(page))
        self.assertIn("accounts-profile-button", page.script)
        self.assertIn("settings|設定|设置", page.script)

    def test_continue_gate_accepts_single_continue_button(self):
        class FakePage:
            def evaluate(self, script):
                self.script = script
                return True

        page = FakePage()
        self.assertTrue(LoginSecretSetupFlow._dismiss_continue_gate(page))
        self.assertIn("buttons.length === 1", page.script)
        self.assertIn("continue|next|finish", page.script)

    def test_chatgpt_page_is_reused_during_login_secret_steps(self):
        class Page:
            url = "https://chatgpt.com/"

            def __init__(self):
                self.visited = []

            def goto(self, url, **_kwargs):
                self.visited.append(url)
                self.url = url

        page = Page()
        LoginSecretSetupFlow._ensure_chatgpt_page(page)
        self.assertEqual(page.visited, [])
        page.url = "https://auth.openai.com/authorize"
        LoginSecretSetupFlow._ensure_chatgpt_page(page)
        self.assertEqual(page.visited, ["https://chatgpt.com"])

    def test_recent_email_code_is_only_usable_for_a_short_window(self):
        now = 1_700_000_000.0
        self.assertTrue(LoginSecretSetupFlow._recent_email_code_usable("123456", now - 30, now))
        self.assertTrue(LoginSecretSetupFlow._recent_email_code_usable("123456", now - RECENT_EMAIL_CODE_MAX_AGE_SECONDS, now))
        self.assertFalse(LoginSecretSetupFlow._recent_email_code_usable("123456", now - RECENT_EMAIL_CODE_MAX_AGE_SECONDS - 1, now))
        self.assertFalse(LoginSecretSetupFlow._recent_email_code_usable("not-code", now - 1, now))

    def test_reauthentication_prefers_recent_registration_code_before_mailbox_reader(self):
        class Flow(LoginSecretSetupFlow):
            def __init__(self):
                super().__init__(self_account, {}, "", recent_email_code="123456", recent_email_code_at=time.time())
                self.submitted = False
                self.used_code = ""

            @staticmethod
            def _page_state(_page):
                if Flow.instance.submitted:
                    return {"url": "https://chatgpt.com/", "passwordInputs": 0, "codeInputs": 0, "text": ""}
                return {"url": "https://auth.openai.com/email-verification", "passwordInputs": 0, "codeInputs": 1, "text": ""}

            @staticmethod
            def _session_json(_page):
                if not Flow.instance.submitted:
                    raise RuntimeError("not submitted")
                return {"accessToken": "access-token"}

            def _fill_code(self, page, code):
                self.used_code = code
                self.submitted = True
                page.url = "https://chatgpt.com/"
                return True

            def _reader_instance(self):
                raise AssertionError("recent code should be used before reading the mailbox")

            def _sleep(self, _seconds):
                return None

        self_account = self._account()
        Flow.instance = Flow()
        class Page:
            url = "https://auth.openai.com/email-verification"

        page = Page()
        Flow.instance._complete_reauthentication(
            page,
            time.time(),
            "ChatGPT-password",
            recent_email_code="123456",
            recent_email_code_at=time.time(),
        )
        self.assertEqual(Flow.instance.used_code, "123456")

    def test_reauthentication_rejects_recent_code_once_then_waits_for_distinct_code(self):
        class Reader:
            def __init__(self):
                self.codes = iter(("123456", "654321"))
                self.timestamps = []

            def wait_for_code(self, timestamp, *_args):
                self.timestamps.append(timestamp)
                return next(self.codes)

        class Flow(LoginSecretSetupFlow):
            def __init__(self):
                super().__init__(self_account, {}, "")
                self.reader_stub = Reader()
                self.submitted_codes = []
                self.logs = []

            def _page_state(self, _page):
                if self.submitted_codes == ["123456"]:
                    return {
                        "url": "https://auth.openai.com/email-verification",
                        "passwordInputs": 0,
                        "codeInputs": 1,
                        "text": "Wrong code. Please check it and try again.",
                    }
                return {
                    "url": "https://auth.openai.com/email-verification",
                    "passwordInputs": 0,
                    "codeInputs": 1,
                    "text": "",
                }

            def _session_json(self, _page):
                if self.submitted_codes != ["123456", "654321"]:
                    raise RuntimeError("not authenticated")
                return {"accessToken": "access-token"}

            def _reader_instance(self):
                return self.reader_stub

            def _fill_code(self, page, code):
                self.submitted_codes.append(code)
                if code == "654321":
                    page.url = "https://chatgpt.com/"
                return True

            def _sleep(self, _seconds):
                return None

        self_account = self._account()
        flow = Flow()
        flow.log = flow.logs.append
        page = type("Page", (), {"url": "https://auth.openai.com/email-verification"})()

        flow._complete_reauthentication(
            page,
            time.time(),
            "ChatGPT-password",
            recent_email_code="123456",
            recent_email_code_at=time.time(),
        )

        self.assertEqual(flow.submitted_codes, ["123456", "654321"])
        self.assertGreaterEqual(len(flow.reader_stub.timestamps), 2)
        self.assertEqual(flow.reader_stub.timestamps[0], flow.reader_stub.timestamps[-1])
        self.assertEqual(flow.logs.count("[登录密钥] 优先复用本次注册刚使用的邮箱验证码"), 1)
        self.assertEqual(flow.logs.count("[登录密钥] 注册阶段验证码无法用于重认证，将等待新的邮箱验证码"), 1)

    def test_password_reauthentication_always_reads_a_fresh_mailbox_code(self):
        class Reader:
            def wait_for_code(self, min_timestamp):
                self.min_timestamp = min_timestamp
                return "654321"

        class Flow(LoginSecretSetupFlow):
            def __init__(self):
                super().__init__(
                    self_account,
                    {},
                    "",
                    recent_email_code="123456",
                    recent_email_code_at=time.time(),
                )
                self.reader_stub = Reader()
                self.used_code = ""
                self.submitted = False

            @staticmethod
            def _page_state(_page):
                if Flow.instance.submitted:
                    return {"url": "https://chatgpt.com/", "passwordInputs": 0, "codeInputs": 0, "text": ""}
                return {"url": "https://auth.openai.com/email-verification", "passwordInputs": 0, "codeInputs": 1, "text": ""}

            @staticmethod
            def _session_json(_page):
                if not Flow.instance.submitted:
                    raise RuntimeError("not submitted")
                return {"accessToken": "access-token"}

            def _reader_instance(self):
                return self.reader_stub

            def _fill_code(self, page, code):
                self.used_code = code
                self.submitted = True
                page.url = "https://chatgpt.com/"
                return True

            def _sleep(self, _seconds):
                return None

        self_account = self._account()
        Flow.instance = Flow()
        page = type("Page", (), {"url": "https://auth.openai.com/email-verification"})()
        Flow.instance._complete_reauthentication(
            page,
            time.time(),
            "ChatGPT-password",
            recent_email_code="123456",
            recent_email_code_at=time.time(),
            force_fresh_email_code=True,
        )
        self.assertEqual(Flow.instance.used_code, "654321")
        self.assertGreaterEqual(Flow.instance.reader_stub.min_timestamp, time.time() - 2)

    def test_password_reauthentication_requests_post_login_add_password_flow(self):
        class FakePage:
            def __init__(self):
                self.visited = []
                self.scripts = []

            def goto(self, url, **_kwargs):
                self.visited.append(url)
                self.url = url

            def evaluate(self, script, _payload=None):
                self.scripts.append(script)
                if "/api/auth/signin/openai" in script:
                    return {"ok": True, "status": 200, "data": {"url": "https://auth.openai.com/authorize"}}
                if "email-otp/validate" in script:
                    return {"ok": True, "status": 200, "data": {"continue_url": "https://chatgpt.com/api/auth/callback/openai"}}
                raise AssertionError("unexpected browser request")

        class Flow(LoginSecretSetupFlow):
            def __init__(self):
                super().__init__(self_account, {}, "")
                self.used_code = ""

            def _reader_instance(self):
                class Reader:
                    def wait_for_code(inner, _min_timestamp):
                        self.used_code = "654321"
                        return self.used_code

                return Reader()

            @staticmethod
            def _session_json(_page):
                return {"accessToken": "access-token"}

        self_account = self._account()
        page = FakePage()
        flow = Flow()
        result = flow._reauth_for_password(page, "ChatGPT-password")
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual(flow.used_code, "654321")
        self.assertTrue(any("post_login_add_password:'true'" in script for script in page.scripts))
        self.assertTrue(any("action=add_password" in script for script in page.scripts))
        self.assertTrue(any("email-otp/validate" in script for script in page.scripts))

    def test_browser_two_factor_reauthentication_uses_same_recent_code_strategy(self):
        class Page:
            url = "https://chatgpt.com/"

            def evaluate(self, script, _payload=None):
                if "/api/auth/signin/openai" in script:
                    return {"ok": True, "status": 200, "data": {"url": "https://auth.openai.com/authorize"}}
                if "/api/auth/csrf" in script:
                    return {"ok": True, "status": 200, "data": {"csrfToken": "csrf-token"}}
                raise AssertionError("unexpected browser request")

        flow = LoginSecretSetupFlow(
            self._account(),
            {},
            "",
            recent_email_code="123456",
            recent_email_code_at=time.time(),
        )
        reauthenticate = Mock(return_value={"accessToken": "access-token"})
        flow._reauthenticate_with_fresh_email_code = reauthenticate

        flow._reauth_for_2fa(
            Page(),
            "ChatGPT-password",
            recent_email_code="123456",
            recent_email_code_at=flow.recent_email_code_at,
        )

        self.assertTrue(reauthenticate.call_args.kwargs["prefer_recent_email_code"])
        self.assertEqual(reauthenticate.call_args.kwargs["recent_email_code"], "123456")

    def test_browser_reauthentication_reads_new_code_after_old_code_is_rejected(self):
        class Reader:
            def __init__(self):
                self.codes = iter(("111111", "222222"))

            def wait_for_code(self, _timestamp):
                return next(self.codes)

        class Page:
            def __init__(self):
                self.url = ""
                self.codes = []

            def goto(self, url, **_kwargs):
                self.url = url

            def evaluate(self, _script, code):
                self.codes.append(code)
                if len(self.codes) == 1:
                    return {"ok": False, "status": 401, "data": {"code": "wrong_email_otp_code", "message": "Wrong code"}}
                return {"ok": True, "status": 200, "data": {"continue_url": "https://chatgpt.com/api/auth/callback/openai"}}

        class Flow(LoginSecretSetupFlow):
            def __init__(self):
                super().__init__(self_account, {}, "")
                self.reader = Reader()

            @staticmethod
            def _session_json(_page):
                return {"accessToken": "access-token"}

        self_account = self._account()
        page = Page()
        result = Flow()._reauthenticate_with_fresh_email_code(page, "https://auth.openai.com/authorize", time.time())
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual(page.codes, ["111111", "222222"])

    def test_protocol_reauthentication_reads_new_code_after_old_code_is_rejected(self):
        class Response:
            def __init__(self, status, data=None):
                self.status_code = status
                self._data = data
                self.text = json.dumps(data or {})

            def json(self):
                return self._data

        class Reader:
            def __init__(self):
                self.codes = iter(("111111", "222222"))

            def wait_for_code(self, _timestamp, _timeout):
                return next(self.codes)

        class Http:
            def __init__(self):
                self.validation_codes = []

            def request(self, method, url, **kwargs):
                if method == "GET" and url.endswith("/api/auth/csrf"):
                    return Response(200, {"csrfToken": "csrf-token"})
                if method == "POST" and "/api/auth/signin/openai?" in url:
                    return Response(200, {"url": "https://auth.openai.com/authorize"})
                if method == "GET" and "auth.openai.com/authorize" in url:
                    return Response(200, {})
                if method == "POST" and url.endswith("/api/accounts/email-otp/validate"):
                    code = kwargs["json"]["code"]
                    self.validation_codes.append(code)
                    if len(self.validation_codes) == 1:
                        return Response(401, {"code": "wrong_email_otp_code", "message": "Wrong code"})
                    return Response(200, {"continue_url": "https://chatgpt.com/api/auth/callback/openai"})
                if method == "GET" and "chatgpt.com/api/auth/callback" in url:
                    return Response(200, {})
                if method == "GET" and url.endswith("/api/auth/session"):
                    return Response(200, {"accessToken": "access-token"})
                raise AssertionError(f"unexpected request: {method} {url}")

        class Flow(ProtocolLoginSecretSetupFlow):
            def __init__(self):
                super().__init__(self_account, {}, http)
                self.reader = Reader()

        self_account = self._account()
        http = Http()
        result = Flow()._reauthenticate("https://chatgpt.com/?action=add_password")
        self.assertEqual(result["accessToken"], "access-token")
        self.assertEqual(http.validation_codes, ["111111", "222222"])

    def test_totp_protocol_setup_uses_existing_session_without_reauthentication(self):
        class FakePage:
            def __init__(self):
                self.info_calls = 0
                self.calls = []

            def goto(self, url, **_kwargs):
                self.url = url

            def evaluate(self, script, payload=None):
                if "/api/auth/session" in script:
                    return {"ok": True, "status": 200, "data": {"accessToken": "access-token"}}
                if "mfa_info" in script:
                    self.info_calls += 1
                    enabled = self.info_calls > 1
                    factors = [{"id": "factor-id", "factor_type": "totp"}] if enabled else []
                    return {"ok": True, "status": 200, "data": {"mfa_enabled": enabled, "factors": {"totp": factors}}}
                if "accounts/mfa/enroll" in script:
                    self.calls.append("enroll")
                    return {"ok": True, "status": 200, "data": {"secret": "JBSWY3DPEHPK3PXP", "session_id": "session-id", "factor": {"id": "factor-id"}}}
                if "activate_enrollment" in script:
                    self.calls.append(("activate", payload["code"], payload["sessionId"]))
                    return {"ok": True, "status": 200, "data": {"success": True}}
                raise AssertionError("unexpected browser request")

        class Flow(LoginSecretSetupFlow):
            def _fresh_totp_code(self, _secret, **_kwargs):
                return "123456"

            def _reauth_for_2fa(self, _page, _password, **_kwargs):
                raise AssertionError("valid session must not be reauthenticated")

        page = FakePage()
        flow = Flow(self._account(), {}, "")
        secret, session = flow._setup_2fa(page, "ChatGPT-password")
        self.assertEqual(secret, "JBSWY3DPEHPK3PXP")
        self.assertEqual(session["accessToken"], "access-token")
        self.assertEqual(page.calls, ["enroll", ("activate", "123456", "session-id")])
        self.assertEqual(flow.account.totp_secret, secret)

    def test_totp_protocol_reauthenticates_once_only_after_unauthorized_response(self):
        class FakePage:
            def __init__(self):
                self.authorized = False
                self.info_calls = 0

            def goto(self, url, **_kwargs):
                self.url = url

            def evaluate(self, script, _payload=None):
                if "/api/auth/session" in script:
                    return {"ok": True, "status": 200, "data": {"accessToken": "access-token"}}
                if "mfa_info" in script:
                    if not self.authorized:
                        return {"ok": False, "status": 401, "data": {}}
                    self.info_calls += 1
                    enabled = self.info_calls > 1
                    factors = [{"id": "factor-id", "factor_type": "totp"}] if enabled else []
                    return {"ok": True, "status": 200, "data": {"mfa_enabled": enabled, "factors": {"totp": factors}}}
                if "accounts/mfa/enroll" in script:
                    return {"ok": True, "status": 200, "data": {"secret": "JBSWY3DPEHPK3PXP", "session_id": "session-id", "factor": {"id": "factor-id"}}}
                if "activate_enrollment" in script:
                    return {"ok": True, "status": 200, "data": {"success": True}}
                raise AssertionError("unexpected browser request")

        class Flow(LoginSecretSetupFlow):
            reauth_count = 0

            def _fresh_totp_code(self, _secret, **_kwargs):
                return "123456"

            def _reauth_for_2fa(self, page, _password, **_kwargs):
                self.reauth_count += 1
                page.authorized = True
                return {"accessToken": "new-access-token"}

        page = FakePage()
        flow = Flow(self._account(), {}, "")
        secret, _session = flow._setup_2fa(page, "ChatGPT-password")
        self.assertEqual(secret, "JBSWY3DPEHPK3PXP")
        self.assertEqual(flow.reauth_count, 1)

    def test_totp_secret_is_not_saved_until_mfa_info_confirms_activation(self):
        class FakePage:
            def evaluate(self, script, _payload=None):
                if "mfa_info" in script:
                    return {"ok": True, "status": 200, "data": {"mfa_enabled": False, "factors": {"totp": []}}}
                if "accounts/mfa/enroll" in script:
                    return {"ok": True, "status": 200, "data": {"secret": "JBSWY3DPEHPK3PXP", "session_id": "session-id", "factor": {"id": "factor-id"}}}
                if "activate_enrollment" in script:
                    return {"ok": True, "status": 200, "data": {"success": True}}
                raise AssertionError("unexpected browser request")

        class Flow(LoginSecretSetupFlow):
            def _fresh_totp_code(self, _secret, **_kwargs):
                return "123456"

        flow = Flow(self._account(), {}, "")
        with self.assertRaisesRegex(RuntimeError, "mfa_info 未确认"):
            flow._setup_2fa_protocol(FakePage(), "access-token")
        self.assertEqual(flow.account.totp_secret, "")


if __name__ == "__main__":
    unittest.main()
