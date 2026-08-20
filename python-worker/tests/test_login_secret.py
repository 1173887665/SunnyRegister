import unittest

from sunny_core.login_secret import LoginSecretSetupFlow, generate_chatgpt_password
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

            def _reauth_for_2fa(self, _page, _password):
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

            def _reauth_for_2fa(self, page, _password):
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
