import unittest

from sunny_core.login_secret import LoginSecretSetupFlow, generate_chatgpt_password
from sunny_core.mailbox import MailAccount


class LoginSecretTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
