import unittest
import time

from sunny_core.login_secret import RECENT_EMAIL_CODE_MAX_AGE_SECONDS, LoginSecretSetupFlow, generate_chatgpt_password
from sunny_core.mailbox import MailAccount


class LoginSecretTests(unittest.TestCase):
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
                raise AssertionError("unexpected browser request")

        class Flow(LoginSecretSetupFlow):
            def __init__(self):
                super().__init__(self_account, {}, "")
                self.reauth_args = None

            def _complete_reauthentication(self, page, min_timestamp, password, **kwargs):
                self.reauth_args = (page, min_timestamp, password, kwargs)

            @staticmethod
            def _session_json(_page):
                return {"accessToken": "access-token"}

        self_account = self._account()
        page = FakePage()
        flow = Flow()
        result = flow._reauth_for_password(page, "ChatGPT-password")
        self.assertEqual(result["accessToken"], "access-token")
        self.assertTrue(any("post_login_add_password:'true'" in script for script in page.scripts))
        self.assertTrue(any("action=add_password" in script for script in page.scripts))
        self.assertTrue(flow.reauth_args[3]["force_fresh_email_code"])

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
