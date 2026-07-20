from __future__ import annotations

import unittest
from unittest.mock import ANY, MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

from sunny_core import worker
from sunny_core.browser_backend import open_registration_browser
from sunny_core.mailbox import MailAccount
from sunny_core.openai_auth import BrowserDriverDisconnectedError, DEFAULT_REDIRECT_URI, OpenAIEmailRegisterFlow


class FakeDB:
    def __init__(self) -> None:
        self.task_id = "test-task"
        self.mailbox_updates: list[dict] = []
        self.account_updates: list[dict] = []
        self.sessions: list[dict] = []
        self.events: list[tuple] = []
        self.sub2api_updates: list[dict] = []

    def ensure_not_cancelled(self) -> None:
        return None

    def cancel_requested(self) -> bool:
        return False

    def mailbox_status(self, mailbox_id) -> str:
        return self.mailbox_updates[-1]["status"] if self.mailbox_updates else "未注册"

    def event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))

    def mark_mailbox(self, mailbox_id, status, error="", openai_rt="") -> None:
        self.mailbox_updates.append({"id": mailbox_id, "status": status, "error": error, "openai_rt": openai_rt})

    def usable_phone_count(self) -> int:
        return 0

    def smsbower_available(self) -> bool:
        return False

    def smspool_available(self) -> bool:
        return False

    def get_config(self, key) -> dict:
        return {}

    def upsert_account(self, email, **fields) -> int:
        self.account_updates.append({"email": email, **fields})
        return 7

    def upsert_session(self, email, account_id, session, raw="") -> None:
        self.sessions.append({"email": email, "account_id": account_id, "session": session, "raw": raw})

    def set_account_sub2api_status(self, email, status, sub2api_id="", error="") -> None:
        self.sub2api_updates.append({"email": email, "status": status, "sub2api_id": sub2api_id, "error": error})


def mailbox(status="未注册", openai_rt="") -> dict:
    return {
        "id": 1,
        "email": "user@example.com",
        "password": "password",
        "client_id": "client-id",
        "refresh_token": "outlook-refresh-token",
        "openai_rt": openai_rt,
        "raw": "user@example.com----password----client-id----outlook-refresh-token",
        "account_type": "free",
        "status": status,
    }


class StageStatusTests(unittest.TestCase):
    def run_one(self, stage: str, session: dict, status="未注册", import_result=None):
        db = FakeDB()
        payload = {"registration_stage": stage, "execution_mode": "background"}
        import_side_effect = import_result if isinstance(import_result, Exception) else None
        import_value = {} if import_result is None or isinstance(import_result, Exception) else import_result
        with (
            patch.object(worker, "_prepare_register_proxy", return_value={"register": "", "mode": "direct"}),
            patch.object(worker, "login_or_register", return_value=session),
            patch.object(worker, "_import_sub2api", return_value=import_value, side_effect=import_side_effect),
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(status), 1, 1)
        return db, ok, result

    def test_missing_phone_resources_keeps_registered_status(self):
        db, ok, result = self.run_one(worker.CODEX_PHONE_BIND, {"access_token": "access", "auth_action": "register"})
        self.assertTrue(ok)
        self.assertEqual(db.mailbox_updates[-1]["status"], "已注册")
        self.assertEqual(db.account_updates[-1]["status"], "registered")
        self.assertFalse(result["stage_complete"])

    def test_phone_completed_without_rt_keeps_phone_bound_status(self):
        db, ok, result = self.run_one(worker.CODEX_PHONE_BIND, {"access_token": "access", "phone_bound": True, "post_registration_error": "RT failed"})
        self.assertTrue(ok)
        self.assertEqual(db.mailbox_updates[-1]["status"], "已接码")
        self.assertEqual(db.account_updates[-1]["status"], "phone_bound")
        self.assertFalse(result["stage_complete"])

    def test_reverse_proxy_failure_keeps_phone_bound_status(self):
        db, ok, result = self.run_one(
            worker.IMPORT_REVERSE_PROXY,
            {"access_token": "access", "refresh_token": "rt", "auth_action": "register"},
            import_result=RuntimeError("sub2api unavailable"),
        )
        self.assertTrue(ok)
        self.assertEqual(db.mailbox_updates[-1]["status"], "已接码")
        self.assertEqual(db.account_updates[-1]["status"], "phone_bound")
        self.assertEqual(db.sub2api_updates[-1]["status"], "failed")
        self.assertFalse(result["stage_complete"])

    def test_reverse_proxy_success_sets_reverse_proxied_status(self):
        db, ok, result = self.run_one(
            worker.IMPORT_REVERSE_PROXY,
            {"access_token": "access", "refresh_token": "rt", "auth_action": "register"},
            import_result={"id": "remote-account"},
        )
        self.assertTrue(ok)
        self.assertEqual(db.mailbox_updates[-1]["status"], "已反代")
        self.assertEqual(db.account_updates[-1]["status"], "reverse_proxied")
        self.assertTrue(result["stage_complete"])

    def test_completed_status_does_not_regress(self):
        db, ok, result = self.run_one(worker.REGISTER_ONLY, {"access_token": "access", "auth_action": "login"}, status="已反代")
        self.assertTrue(ok)
        self.assertEqual(db.mailbox_updates[-1]["status"], "已反代")
        self.assertEqual(db.account_updates[-1]["status"], "reverse_proxied")
        self.assertEqual(result["completed_status"], "已反代")

    def test_registration_checkpoint_can_be_saved_before_phone_stage(self):
        db = FakeDB()
        account = MailAccount(
            email="user@example.com",
            password="password",
            client_id="client-id",
            refresh_token="outlook-refresh-token",
            raw="user@example.com----password----client-id----outlook-refresh-token",
        )
        worker._persist_registration_checkpoint(
            db,
            mailbox(),
            account,
            "registered",
            {"access_token": "access-token", "session_json": {"accessToken": "access-token"}},
            "未注册",
        )
        self.assertEqual(db.mailbox_updates[-1]["status"], "已注册")
        self.assertEqual(db.account_updates[-1]["status"], "registered")
        self.assertEqual(len(db.sessions), 1)

class SessionFallbackTests(unittest.TestCase):
    def test_refresh_token_failure_keeps_chatgpt_session(self):
        account = MailAccount(
            email="user@example.com",
            password="password",
            client_id="client-id",
            refresh_token="outlook-refresh-token",
            raw="user@example.com----password----client-id----outlook-refresh-token",
        )
        logs: list[str] = []
        flow = OpenAIEmailRegisterFlow(account, "", True, logs.append, require_refresh_token=True)
        flow.phone_verification_completed = True

        class Context:
            @staticmethod
            def storage_state():
                return {"cookies": []}

        with (
            patch.object(flow, "_read_chatgpt_session_json", return_value={"accessToken": "access-token"}),
            patch.object(flow, "_authorize_rt_from_browser", side_effect=RuntimeError("SMS provider unavailable")),
        ):
            result = flow._extract_session_info(Context(), object())

        self.assertEqual(result["access_token"], "access-token")
        self.assertTrue(result["phone_bound"])
        self.assertIn("Refresh Token", result["post_registration_error"])
        self.assertTrue(any("ChatGPT" in item and "Session" in item for item in logs))

    def test_session_reader_prefers_context_request_without_navigating_page(self):
        account = MailAccount("user@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)
        response = Mock(status=200)
        response.text.return_value = '{"accessToken":"access-token"}'
        context = Mock()
        context.request.get.return_value = response
        page = Mock()
        page.context.browser.is_connected.return_value = True

        result = flow._read_chatgpt_session_json(context, page)

        self.assertEqual(result["accessToken"], "access-token")
        page.goto.assert_not_called()
        page.evaluate.assert_not_called()

    def test_session_reader_does_not_retry_dead_playwright_driver(self):
        account = MailAccount("user@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)
        context = Mock()
        context.request.get.side_effect = RuntimeError("Page.evaluate: Connection closed while reading from the driver")
        page = Mock()
        page.context.browser.is_connected.return_value = True

        with patch.object(flow, "_sleep_checked") as sleep:
            with self.assertRaises(BrowserDriverDisconnectedError):
                flow._read_chatgpt_session_json(context, page)

        self.assertEqual(context.request.get.call_count, 1)
        page.evaluate.assert_not_called()
        sleep.assert_not_called()


class BrowserCsrfTests(unittest.TestCase):
    def test_signin_uses_browser_session_for_csrf_and_post(self):
        account = MailAccount(
            email="user@example.com",
            password="password",
            client_id="client-id",
            refresh_token="outlook-refresh-token",
            raw="user@example.com----password----client-id----outlook-refresh-token",
        )
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)
        context = Mock()
        context.cookies.return_value = [{"name": "oai-did", "value": "device-id"}]

        class Page:
            def evaluate(self, script, payload=None):
                if "/api/auth/csrf" in script:
                    return {"ok": True, "status": 200, "text": '{"csrfToken":"browser-csrf"}'}
                self_payload = payload or {}
                assert self_payload["csrfToken"] == "browser-csrf"
                return {"ok": True, "status": 200, "text": '{"url":"https://auth.openai.com/authorize"}'}

        signin_url = flow._create_openai_signin_url(context, Page())

        self.assertEqual(signin_url, "https://auth.openai.com/authorize")
        context.request.get.assert_not_called()
        context.request.post.assert_not_called()


class BrowserBackendTests(unittest.TestCase):
    def test_background_mode_uses_one_camoufox_incognito_context(self):
        fingerprint = Mock(
            locale="ja-JP",
            languages=["ja-JP", "ja"],
            timezone="Asia/Tokyo",
        )
        manager = MagicMock()
        browser = Mock()
        context = Mock()
        browser.new_context.return_value = context
        manager.__enter__.return_value = browser

        with patch("camoufox.sync_api.Camoufox", return_value=manager) as camoufox:
            with open_registration_browser(
                headless=True,
                proxy_url="http://user:pass@proxy.example:8080",
                fingerprint=fingerprint,
                log=lambda _message: None,
            ) as session:
                self.assertEqual(session.backend, "camoufox")
                self.assertIs(session.context, context)

        options = camoufox.call_args.kwargs
        self.assertTrue(options["headless"])
        self.assertTrue(options["humanize"])
        self.assertEqual(options["locale"], ["ja-JP", "ja"])
        self.assertEqual(options["proxy"]["server"], "http://proxy.example:8080")
        self.assertTrue(options["geoip"])
        browser.new_context.assert_called_once_with(
            no_viewport=True,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        context.close.assert_called_once()
        manager.__exit__.assert_called_once()

    def test_disconnected_camoufox_skips_duplicate_browser_close(self):
        fingerprint = Mock(locale="ja-JP", languages=["ja-JP", "ja"], timezone="Asia/Tokyo")
        manager = MagicMock()
        browser = Mock()
        browser.is_connected.return_value = False
        context = Mock()
        browser.new_context.return_value = context
        manager.__enter__.return_value = browser

        with patch("camoufox.sync_api.Camoufox", return_value=manager):
            with open_registration_browser(
                headless=True,
                proxy_url="",
                fingerprint=fingerprint,
                log=lambda _message: None,
            ):
                pass

        context.close.assert_not_called()
        self.assertIsNone(manager.browser)
        manager.__exit__.assert_called_once()


class BrowserOAuthCallbackTests(unittest.TestCase):
    @staticmethod
    def make_flow(logs: list[str] | None = None) -> OpenAIEmailRegisterFlow:
        account = MailAccount(
            email="user@example.com",
            password="password",
            client_id="client-id",
            refresh_token="outlook-refresh-token",
            raw="user@example.com----password----client-id----outlook-refresh-token",
        )
        return OpenAIEmailRegisterFlow(account, "", True, (logs if logs is not None else []).append)

    def test_callback_requires_matching_oauth_state(self):
        flow = self.make_flow()
        callback_url = f"{DEFAULT_REDIRECT_URI}?code=auth-code&state=expected-state"

        result = flow._extract_oauth_callback_from_url(callback_url, "expected-state")

        self.assertEqual(result["code"], "auth-code")
        with self.assertRaisesRegex(RuntimeError, "state mismatch"):
            flow._extract_oauth_callback_from_url(callback_url, "other-state")

    def test_attribute_based_consent_submit_captures_callback_before_chrome_error(self):
        logs: list[str] = []
        flow = self.make_flow(logs)

        class Request:
            def __init__(self, url: str):
                self.url = url

        class Route:
            def __init__(self, url: str):
                self.request = Request(url)
                self.fulfilled = False

            def fulfill(self, **_kwargs):
                self.fulfilled = True

        class Page:
            def __init__(self):
                self.url = "about:blank"
                self.listeners = {}
                self.route_handler = None
                self.callback_fulfilled = False

            def on(self, event, handler):
                self.listeners[event] = handler

            def route(self, _pattern, handler):
                self.route_handler = handler

            def goto(self, oauth_url, **_kwargs):
                self.url = "https://auth.openai.com/sign-in-with-chatgpt/codex/consent"
                self.oauth_state = parse_qs(urlparse(oauth_url).query)["state"][0]

            def evaluate(self, script):
                self.uses_stable_submit_identity = (
                    'data-dd-action-name="Continue"' in script
                    and "form.requestSubmit(target)" in script
                    and "缍氳" in script
                )
                callback_url = f"{DEFAULT_REDIRECT_URI}?code=auth-code&state={self.oauth_state}"
                route = Route(callback_url)
                self.route_handler(route)
                self.callback_fulfilled = route.fulfilled
                self.url = "chrome-error://chromewebdata/"
                return self.uses_stable_submit_identity

            def unroute(self, *_args):
                return None

            def remove_listener(self, *_args):
                return None

        page = Page()
        with (
            patch.object(flow, "_has_phone_form", return_value=False),
            patch.object(flow, "_sleep_checked", return_value=None),
            patch.object(flow, "_exchange_browser_code_for_token", return_value={"refresh_token": "rt_test"}) as exchange,
        ):
            result = flow._authorize_rt_from_browser(Mock(), page)

        self.assertEqual(result["refresh_token"], "rt_test")
        self.assertTrue(page.callback_fulfilled)
        exchange.assert_called_once_with(ANY, "auth-code", ANY)
        self.assertTrue(page.callback_fulfilled)


class BrowserEmailOTPSubmitTests(unittest.TestCase):
    def test_camoufox_email_otp_prefers_native_form_submit(self):
        account = MailAccount(
            email="user@example.com",
            password="password",
            client_id="client-id",
            refresh_token="outlook-refresh-token",
            raw="user@example.com----password----client-id----outlook-refresh-token",
        )
        logs: list[str] = []
        flow = OpenAIEmailRegisterFlow(account, "", True, logs.append)
        flow.otp_reader = Mock()
        flow.otp_reader.wait_for_code.return_value = "123456"
        otp_input = Mock()

        with (
            patch.object(flow, "_visible_inputs", return_value=[otp_input]),
            patch.object(flow, "_submit_email_code_form", return_value=True) as native_submit,
            patch.object(flow, "_validate_email_code_api", return_value="") as api_submit,
            patch.object(flow, "_wait_after_otp_submit") as wait_transition,
        ):
            flow._submit_email_code(Mock(), 0)

        self.assertEqual(otp_input.fill.call_args_list[-1].args[0], "123456")
        native_submit.assert_called_once()
        api_submit.assert_not_called()
        wait_transition.assert_called_once()
        self.assertTrue(any("Camoufox" in item for item in logs))

    def test_email_otp_falls_back_to_native_then_json_api_on_html_route_error(self):
        account = MailAccount(
            email="user@example.com",
            password="password",
            client_id="client-id",
            refresh_token="outlook-refresh-token",
            raw="user@example.com----password----client-id----outlook-refresh-token",
        )
        flow = OpenAIEmailRegisterFlow(account, "", False, lambda _message: None)
        flow.otp_reader = Mock()
        flow.otp_reader.wait_for_code.return_value = "123456"
        page = Mock()
        otp_input = Mock()

        with (
            patch.object(flow, "_visible_inputs", return_value=[otp_input]),
            patch.object(flow, "_submit_email_code_form", return_value=True),
            patch.object(flow, "_wait_after_otp_submit", side_effect=[RuntimeError("Route Error (400 Invalid content type: text/html; charset=UTF-8)"), None]) as wait_transition,
            patch.object(flow, "_retry_email_code_page_submit_after_route_error", return_value=False) as retry_page_submit,
            patch.object(flow, "_validate_email_code_api", side_effect=[RuntimeError("temporary api error"), "https://chatgpt.com/"]) as api_submit,
        ):
            flow._submit_email_code(page, 0)

        retry_page_submit.assert_called_once_with(page, "123456")
        self.assertEqual(api_submit.call_count, 2)
        page.goto.assert_called_once_with("https://chatgpt.com/", wait_until="domcontentloaded", timeout=90000)
        self.assertEqual(wait_transition.call_count, 2)

    def test_camoufox_email_otp_uses_sentinel_api_after_native_submit_stalls(self):
        account = MailAccount("user@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)
        flow.otp_reader = Mock()
        flow.otp_reader.wait_for_code.return_value = "123456"
        page = Mock()

        with (
            patch.object(flow, "_visible_inputs", return_value=[Mock()]),
            patch.object(flow, "_submit_email_code_form", return_value=True) as native_submit,
            patch.object(flow, "_wait_after_otp_submit", side_effect=RuntimeError("still on OTP page")),
            patch.object(flow, "_validate_email_code_api", side_effect=RuntimeError("EmailOtpValidate was blocked by Cloudflare")) as api_submit,
        ):
            with self.assertRaisesRegex(RuntimeError, "EmailOtpValidate"):
                flow._submit_email_code(page, 0)

        api_submit.assert_called_once()
        native_submit.assert_called_once()

    def test_email_otp_api_attaches_sentinel_and_device_headers(self):
        account = MailAccount("user@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)
        flow.device_id = "device-id"
        page = Mock()
        page.url = "https://auth.openai.com/email-verification"
        page.evaluate.return_value = "Mozilla/5.0 Firefox/135.0"
        response = {"ok": True, "status": 200, "text": "{}", "data": {"continue_url": "https://auth.openai.com/about-you"}}

        with (
            patch("sunny_core.openai_auth.build_sentinel_token", return_value="sentinel-token") as build_token,
            patch("sunny_core.openai_auth.browser_fetch", return_value=response) as fetch,
        ):
            next_url = flow._validate_email_code_api(page, "123456")

        self.assertEqual(next_url, "https://auth.openai.com/about-you")
        build_token.assert_called_once_with(page, "device-id", "email_otp_validate", "Mozilla/5.0 Firefox/135.0")
        headers = fetch.call_args.kwargs["headers"]
        self.assertEqual(headers["openai-sentinel-token"], "sentinel-token")
        self.assertEqual(headers["oai-device-id"], "device-id")

    def test_native_otp_submit_uses_stable_identifiers_and_clicks_submitter(self):
        account = MailAccount("user@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)

        class Page:
            def evaluate(self, script):
                return (
                    'data-dd-action-name="Continue"' in script
                    and 'name="intent"][value="validate"' in script
                    and "submitter.click()" in script
                    and "form.requestSubmit(submitter)" in script
                )

        with patch.object(flow, "_submit_email_code_by_locator", return_value=False):
            self.assertTrue(flow._submit_email_code_form(Page()))

class Sub2APIImportPayloadTests(unittest.TestCase):
    def test_oauth_protocol_fields_are_forwarded_to_sub2api(self):
        db = Mock()
        db.get_config.return_value = {
            "enabled": True,
            "base_url": "https://sub2api.example",
            "admin_token": "admin-key",
            "name_prefix": "Sunny-",
            "group_ids": [2, 3],
            "concurrency": 5,
            "priority": 1,
        }
        response = Mock(status_code=200, text='{"id":"remote-id"}')
        response.json.return_value = {"id": "remote-id"}
        session = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
            "client_id": "client-id",
            "chatgpt_account_id": "account-id",
            "chatgpt_user_id": "user-id",
            "organization_id": "org-id",
            "plan_type": "plus",
            "expires_at": 123456789,
        }

        with patch.object(worker.requests, "post", return_value=response) as post:
            result = worker._import_sub2api(db, "user@example.com", 7, session)

        self.assertEqual(result["id"], "remote-id")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["credentials"]["client_id"], "client-id")
        self.assertEqual(payload["credentials"]["chatgpt_account_id"], "account-id")
        self.assertEqual(payload["credentials"]["chatgpt_user_id"], "user-id")
        self.assertEqual(payload["credentials"]["organization_id"], "org-id")
        self.assertEqual(payload["credentials"]["plan_type"], "plus")
        self.assertEqual(payload["credentials"]["expires_at"], 123456789)
        self.assertEqual(payload["extra"]["import_source"], "sunnyregister_oauth_code")


if __name__ == "__main__":
    unittest.main()
