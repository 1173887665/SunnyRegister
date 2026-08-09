from __future__ import annotations

import unittest
from unittest.mock import ANY, MagicMock, Mock, patch
from urllib.parse import parse_qs, urlparse

from sunny_core import worker
from sunny_core.agent_identity import AgentIdentityUnavailableError
from sunny_core.browser_backend import open_registration_browser
from sunny_core.mailbox import MailAccount
from sunny_core.openai_auth import BrowserDriverDisconnectedError, DEFAULT_REDIRECT_URI, OpenAIEmailRegisterFlow
from sunny_core.protocol_auth import ProtocolChallengeRequired, ProtocolRegistrationError


class FakeDB:
    def __init__(self, configs=None) -> None:
        self.task_id = "test-task"
        self.configs = configs or {}
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
        return self.configs.get(key, {})

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

    def test_proxy_pool_exhaustion_fails_only_current_mailbox(self):
        db = FakeDB()
        payload = {"registration_stage": worker.REGISTER_ONLY, "execution_mode": "background"}
        with (
            patch.object(worker, "_prepare_register_proxy", side_effect=RuntimeError("代理池中没有可用代理")),
            patch.object(worker, "login_or_register") as executor,
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(), 1, 2)

        self.assertFalse(ok)
        self.assertIn("代理池中没有可用代理", str(result))
        self.assertEqual(db.mailbox_updates[-1]["status"], "失败")
        self.assertEqual(db.account_updates[-1]["status"], "failed")
        executor.assert_not_called()

    def test_proxy_pool_exhaustion_preserves_registered_mailbox_status(self):
        db = FakeDB()
        payload = {"registration_stage": worker.REGISTER_ONLY, "execution_mode": "background"}
        with patch.object(worker, "_prepare_register_proxy", side_effect=RuntimeError("代理池中没有可用代理")):
            ok, result = worker._run_one(db, "sunny_login", payload, mailbox(status="已注册"), 1, 1)

        self.assertFalse(ok)
        self.assertIn("代理池中没有可用代理", str(result))
        self.assertEqual(db.mailbox_updates[-1]["status"], "已注册")
        self.assertEqual(db.account_updates[-1]["status"], "registered")

    def test_protocol_mode_dispatches_without_browser_executor(self):
        db = FakeDB()
        payload = {"registration_stage": worker.REGISTER_ONLY, "execution_mode": "protocol"}
        session = {
            "access_token": "protocol-access",
            "auth_action": "register",
            "plan_type": "plus",
            "session_json": {"accessToken": "protocol-access", "account": {"planType": "plus"}},
        }
        with (
            patch.object(worker, "_prepare_register_proxy", return_value={"register": "", "mode": "direct"}),
            patch.object(worker, "login_or_register_protocol", return_value=session) as protocol_executor,
            patch.object(worker, "login_or_register") as browser_executor,
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(), 1, 1)

        self.assertTrue(ok)
        self.assertTrue(result["stage_complete"])
        browser_executor.assert_not_called()
        protocol_executor.assert_called_once()
        self.assertEqual(protocol_executor.call_args.kwargs["challenge_strategy"], "native_headless")
        self.assertEqual(db.account_updates[-1]["account_type"], "plus")

    def test_protocol_challenge_falls_back_to_headless_browser_only(self):
        db = FakeDB()
        payload = {"registration_stage": worker.REGISTER_ONLY, "execution_mode": "protocol"}
        challenge = ProtocolChallengeRequired("Sentinel requires a browser challenge")
        challenge.traffic = {"requests": 4, "total_bytes": 2048}
        browser_session = {
            "access_token": "browser-access",
            "auth_action": "register",
            "plan_type": "free",
            "session_json": {"accessToken": "browser-access"},
        }
        with (
            patch.object(worker, "_prepare_register_proxy", return_value={"register": "http://proxy.example:8080", "mode": "pool"}),
            patch.object(worker, "login_or_register_protocol", side_effect=challenge) as protocol_executor,
            patch.object(worker, "login_or_register", return_value=browser_session) as browser_executor,
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(), 1, 1)

        self.assertTrue(ok)
        self.assertTrue(result["stage_complete"])
        protocol_executor.assert_called_once()
        browser_executor.assert_called_once()
        args, kwargs = browser_executor.call_args
        self.assertEqual(args[1], "http://proxy.example:8080")
        self.assertIs(args[2], True)
        self.assertIsNone(kwargs["phone_provider"])
        self.assertFalse(kwargs["require_refresh_token"])
        self.assertEqual(kwargs["execution_mode"], "protocol_headless_fallback")
        self.assertEqual(db.sessions[-1]["session"]["execution_mode"], "protocol_headless_fallback")
        self.assertEqual(db.sessions[-1]["session"]["protocol_fallback"], "headless")
        self.assertEqual(db.sessions[-1]["session"]["protocol_traffic"]["total_bytes"], 2048)
        self.assertTrue(any("后台无头浏览器" in str(args[0]) for args, _kwargs in db.events))

    def test_protocol_non_challenge_error_does_not_start_browser(self):
        db = FakeDB()
        payload = {"registration_stage": worker.REGISTER_ONLY, "execution_mode": "protocol"}
        with (
            patch.object(worker, "_prepare_register_proxy", return_value={"register": "", "mode": "direct"}),
            patch.object(worker, "login_or_register_protocol", side_effect=ProtocolRegistrationError("invalid protocol response")),
            patch.object(worker, "login_or_register") as browser_executor,
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(), 1, 1)

        self.assertFalse(ok)
        self.assertIn("invalid protocol response", str(result))
        browser_executor.assert_not_called()

    def test_sentinel_protocol_strategy_does_not_start_full_browser_on_challenge(self):
        db = FakeDB()
        payload = {
            "registration_stage": worker.REGISTER_ONLY,
            "execution_mode": "protocol",
            "protocol_challenge_strategy": "sentinel_protocol",
        }
        challenge = ProtocolChallengeRequired("Sentinel runtime failed")
        challenge.traffic = {"requests": 2, "total_bytes": 512}
        with (
            patch.object(worker, "_prepare_register_proxy", return_value={"register": "", "mode": "direct"}),
            patch.object(worker, "login_or_register_protocol", side_effect=challenge) as protocol_executor,
            patch.object(worker, "login_or_register") as browser_executor,
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(), 1, 1)

        self.assertFalse(ok)
        self.assertIn("Sentinel runtime failed", str(result))
        self.assertEqual(protocol_executor.call_args.kwargs["challenge_strategy"], "sentinel_protocol")
        browser_executor.assert_not_called()

    def test_protocol_mode_continues_phone_stage_with_headless_oauth(self):
        db = FakeDB()
        payload = {"registration_stage": worker.CODEX_PHONE_BIND, "execution_mode": "protocol"}
        protocol_session = {"access_token": "protocol-access", "auth_action": "register"}
        completed_session = {
            "access_token": "browser-access",
            "refresh_token": "refresh-token",
            "phone_bound": True,
            "auth_action": "login",
        }
        phone_provider = Mock()
        with (
            patch.object(worker, "_prepare_register_proxy", return_value={"register": "", "mode": "direct"}),
            patch.object(worker, "_combined_phone_provider", return_value=phone_provider) as phone_allocator,
            patch.object(worker, "login_or_register_protocol", return_value=protocol_session),
            patch.object(worker, "login_or_register", return_value=completed_session) as browser_executor,
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(), 1, 1)

        self.assertTrue(ok)
        self.assertTrue(result["stage_complete"])
        self.assertEqual(result["completed_status"], "已接码")
        phone_allocator.assert_called_once()
        browser_executor.assert_called_once()
        self.assertIs(browser_executor.call_args.kwargs["phone_provider"], phone_provider)
        self.assertTrue(browser_executor.call_args.kwargs["require_refresh_token"])
        self.assertEqual(browser_executor.call_args.kwargs["execution_mode"], "protocol_post_stage")

    def test_agent_identity_stage_skips_phone_and_imports_with_access_token(self):
        db = FakeDB()
        payload = {"registration_stage": worker.AGENT_IDENTITY_REVERSE_PROXY, "execution_mode": "protocol", "proxy_all_traffic": True}
        session = {"access_token": "protocol-access", "auth_action": "login", "plan_type": "plus"}
        with (
            patch.object(worker, "_prepare_register_proxy", return_value={"register": "http://proxy.example:8080", "mode": "pool"}),
            patch.object(worker, "_combined_phone_provider") as phone_allocator,
            patch.object(worker, "login_or_register_protocol", return_value=session),
            patch.object(worker, "login_or_register") as browser_executor,
            patch.object(worker, "_import_sub2api_agent_identity", return_value={"created": 1}) as importer,
        ):
            ok, result = worker._run_one(db, "sunny_register", payload, mailbox(status="已注册"), 1, 1)

        self.assertTrue(ok)
        self.assertTrue(result["stage_complete"])
        self.assertTrue(result["agent_identity"])
        self.assertEqual(result["completed_status"], "已反代")
        phone_allocator.assert_not_called()
        browser_executor.assert_not_called()
        importer.assert_called_once_with(db, "user@example.com", 7, session, "http://proxy.example:8080")

    def test_agent_identity_import_uses_codex_session_contract(self):
        db = FakeDB({
            "sub2api": {
                "enabled": True,
                "base_url": "https://sub2api.example",
                "admin_token": "admin-secret",
                "name_prefix": "Sunny-",
                "group_ids": [2, "3"],
                "concurrency": 5,
                "priority": 1,
            }
        })
        auth_json = {
            "auth_mode": "agentIdentity",
            "agent_identity": {
                "agent_runtime_id": "runtime-id",
                "agent_private_key": "private-key",
                "account_id": "account-id",
                "chatgpt_user_id": "user-id",
            },
        }
        response = Mock(status_code=200)
        response.json.return_value = {"data": {"created": 1, "updated": 0, "failed": 0, "items": [{"account_id": 91}]}}
        with (
            patch.object(worker, "create_agent_identity_auth", return_value=auth_json) as creator,
            patch.object(worker.requests, "post", return_value=response) as post,
        ):
            result = worker._import_sub2api_agent_identity(
                db,
                "user@example.com",
                7,
                {"access_token": "access-token", "plan_type": "plus"},
                "http://proxy.example:8080",
            )

        self.assertEqual(result["created"], 1)
        creator.assert_called_once_with(
            "access-token",
            email="user@example.com",
            plan_type="plus",
            proxy_url="http://proxy.example:8080",
            should_cancel=db.cancel_requested,
            log=ANY,
        )
        request = post.call_args
        self.assertEqual(request.args[0], "https://sub2api.example/api/v1/admin/accounts/import/codex-session")
        self.assertEqual(request.kwargs["headers"]["X-API-Key"], "admin-secret")
        self.assertEqual(request.kwargs["headers"]["Accept"], "application/json")
        self.assertFalse(request.kwargs["allow_redirects"])
        payload = request.kwargs["json"]
        self.assertEqual(set(payload), {"contents", "update_existing"})
        self.assertTrue(payload["update_existing"])
        self.assertEqual(len(payload["contents"]), 1)
        imported_auth = __import__("json").loads(payload["contents"][0])
        self.assertEqual(imported_auth["auth_mode"], "agentIdentity")
        self.assertEqual(imported_auth["agent_identity"]["agent_runtime_id"], "runtime-id")

    def test_agent_identity_import_falls_back_to_refresh_token_oauth(self):
        db = FakeDB({
            "sub2api": {
                "enabled": True,
                "base_url": "https://sub2api.example",
                "admin_token": "admin-secret",
            }
        })
        fallback_result = {"id": 91}
        with (
            patch.object(
                worker,
                "create_agent_identity_auth",
                side_effect=AgentIdentityUnavailableError("agent_registry_not_enabled"),
            ),
            patch.object(worker, "_import_sub2api", return_value=fallback_result) as fallback,
        ):
            result = worker._import_sub2api_agent_identity(
                db,
                "user@example.com",
                7,
                {"access_token": "access-token", "refresh_token": "refresh-token"},
                "",
            )

        self.assertEqual(result["id"], 91)
        self.assertEqual(result["_sunny_import_mode"], "oauth_refresh_token")
        fallback.assert_called_once()
        self.assertTrue(any("回退到标准 sub2api OAuth 导入" in args[0] for args, _ in db.events))

    def test_agent_identity_import_without_refresh_token_is_actionable(self):
        db = FakeDB({
            "sub2api": {
                "enabled": True,
                "base_url": "https://sub2api.example",
                "admin_token": "admin-secret",
            }
        })
        with patch.object(
            worker,
            "create_agent_identity_auth",
            side_effect=AgentIdentityUnavailableError("agent_registry_not_enabled"),
        ):
            with self.assertRaisesRegex(
                AgentIdentityUnavailableError,
                "没有 Refresh Token.*Codex 接码绑定",
            ):
                worker._import_sub2api_agent_identity(
                    db,
                    "user@example.com",
                    7,
                    {"access_token": "access-token"},
                    "",
                )

    def test_agent_identity_import_reports_html_gateway_response(self):
        db = FakeDB({
            "sub2api": {
                "enabled": True,
                "base_url": "https://sub2api.example",
                "admin_token": "admin-secret",
            }
        })
        auth_json = {
            "auth_mode": "agentIdentity",
            "agent_identity": {
                "agent_runtime_id": "runtime-id",
                "agent_private_key": "private-key",
                "account_id": "account-id",
                "chatgpt_user_id": "user-id",
            },
        }
        response = Mock(
            status_code=200,
            text="<!doctype html><html><head><title>502 Bad gateway</title></head></html>",
            headers={"Content-Type": "text/html; charset=UTF-8"},
            url="https://sub2api.example/api/v1/admin/accounts/import/codex-session",
        )
        response.json.side_effect = ValueError("unexpected character")
        with (
            patch.object(worker, "create_agent_identity_auth", return_value=auth_json),
            patch.object(worker.requests, "post", return_value=response),
        ):
            with self.assertRaisesRegex(RuntimeError, "返回非 JSON 内容.*502 Bad gateway"):
                worker._import_sub2api_agent_identity(
                    db,
                    "user@example.com",
                    7,
                    {"access_token": "access-token", "plan_type": "plus"},
                    "",
                )

    def test_agent_identity_import_does_not_follow_login_redirect(self):
        db = FakeDB({
            "sub2api": {
                "enabled": True,
                "base_url": "https://sub2api.example",
                "admin_token": "admin-secret",
            }
        })
        auth_json = {
            "auth_mode": "agentIdentity",
            "agent_identity": {
                "agent_runtime_id": "runtime-id",
                "agent_private_key": "private-key",
                "account_id": "account-id",
                "chatgpt_user_id": "user-id",
            },
        }
        response = Mock(
            status_code=302,
            text="",
            headers={"Location": "/login"},
            url="https://sub2api.example/api/v1/admin/accounts/import/codex-session",
        )
        with (
            patch.object(worker, "create_agent_identity_auth", return_value=auth_json),
            patch.object(worker.requests, "post", return_value=response) as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "发生重定向到 /login"):
                worker._import_sub2api_agent_identity(
                    db,
                    "user@example.com",
                    7,
                    {"access_token": "access-token", "plan_type": "plus"},
                    "",
                )
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_agent_identity_import_normalizes_api_v1_base_url(self):
        self.assertEqual(
            worker._sub2api_codex_import_url("https://sub2api.example/api/v1"),
            "https://sub2api.example/api/v1/admin/accounts/import/codex-session",
        )
        self.assertEqual(
            worker._sub2api_codex_import_url("https://sub2api.example/api/v1/admin"),
            "https://sub2api.example/api/v1/admin/accounts/import/codex-session",
        )

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

    def test_existing_account_camoufox_otp_uses_single_sentinel_request(self):
        account = MailAccount(
            email="registered@example.com",
            password="password",
            client_id="client-id",
            refresh_token="outlook-refresh-token",
            raw="registered@example.com----password----client-id----outlook-refresh-token",
        )
        logs: list[str] = []
        flow = OpenAIEmailRegisterFlow(account, "", True, logs.append, existing_account=True)
        flow.otp_reader = Mock()
        flow.otp_reader.wait_for_code.return_value = "123456"
        page = Mock()

        with (
            patch.object(flow, "_fill_email_code_inputs") as fill_inputs,
            patch.object(flow, "_submit_email_code_form") as native_submit,
            patch.object(flow, "_validate_email_code_api", return_value="https://chatgpt.com/") as api_submit,
            patch.object(flow, "_wait_after_otp_submit") as wait_transition,
        ):
            flow._submit_email_code(page, 0)

        fill_inputs.assert_not_called()
        native_submit.assert_not_called()
        api_submit.assert_called_once_with(page, "123456")
        page.goto.assert_called_once_with("https://chatgpt.com/", wait_until="domcontentloaded", timeout=90000)
        wait_transition.assert_called_once_with(page)
        self.assertTrue(any("续期登录浏览器会话" in item for item in logs))

    def test_sentinel_required_json_is_classified_as_challenge(self):
        account = MailAccount("registered@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None, existing_account=True)

        self.assertTrue(flow._is_cloudflare_challenge('{"error":{"code":"sentinel_required"}}'))

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

    def test_camoufox_invalid_native_otp_requests_fresh_code_without_api_reuse(self):
        account = MailAccount("user@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)
        flow.otp_reader = Mock()
        flow.otp_reader.wait_for_code.return_value = "123456"
        page = Mock()

        with (
            patch.object(flow, "_visible_inputs", return_value=[Mock()]),
            patch.object(flow, "_submit_email_code_form", return_value=True),
            patch.object(
                flow,
                "_wait_after_otp_submit",
                side_effect=RuntimeError("Still on email verification page: 不正確なコード"),
            ),
            patch.object(flow, "_retry_with_fresh_email_code") as retry_fresh,
            patch.object(flow, "_validate_email_code_api") as api_submit,
        ):
            flow._submit_email_code(page, 0)

        retry_fresh.assert_called_once_with(page, "123456")
        api_submit.assert_not_called()

    def test_email_otp_api_stops_immediately_on_max_attempts(self):
        account = MailAccount("user@example.com", "password", "client-id", "mail-rt", "raw")
        flow = OpenAIEmailRegisterFlow(account, "", True, lambda _message: None)
        page = Mock()
        page.url = "https://auth.openai.com/email-verification"
        page.evaluate.return_value = "Mozilla/5.0 Firefox/135.0"
        response = {
            "ok": False,
            "status": 400,
            "text": '{"error":{"message":"Too many tries.","code":"max_check_attempts"}}',
        }

        with (
            patch("sunny_core.openai_auth.build_sentinel_token", return_value="sentinel-token"),
            patch("sunny_core.openai_auth.browser_fetch", return_value=response) as fetch,
        ):
            with self.assertRaisesRegex(RuntimeError, "尝试次数已达上限"):
                flow._validate_email_code_api(page, "123456")

        fetch.assert_called_once()

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
        db.task_id = "test-task"
        db.get_config.return_value = {
            "enabled": True,
            "base_url": "https://sub2api.example",
            "admin_token": "admin-key",
            "name_prefix": "Sunny-",
            "group_ids": [2, 3],
            "concurrency": 5,
            "priority": 1,
        }
        response = Mock(status_code=200, text='{"success":1,"failed":0}')
        response.json.return_value = {"success": 1, "failed": 0, "results": []}
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

        self.assertEqual(result["success"], 1)
        request_body = post.call_args.kwargs["json"]
        self.assertEqual(len(request_body["accounts"]), 1)
        payload = request_body["accounts"][0]
        self.assertEqual(payload["credentials"]["client_id"], "client-id")
        self.assertEqual(payload["credentials"]["chatgpt_account_id"], "account-id")
        self.assertEqual(payload["credentials"]["chatgpt_user_id"], "user-id")
        self.assertEqual(payload["credentials"]["organization_id"], "org-id")
        self.assertEqual(payload["credentials"]["plan_type"], "plus")
        self.assertEqual(payload["credentials"]["expires_at"], 123456789)
        self.assertIn("gpt-5.6-sol", payload["credentials"]["model_mapping"])
        self.assertEqual(payload["extra"]["import_source"], "sunnyregister_oauth_code")
        self.assertTrue(post.call_args.kwargs["headers"]["Idempotency-Key"].startswith("sunny-test-task-7-"))

    def test_batch_import_retries_transient_failure_and_requires_confirmation(self):
        db = Mock()
        db.task_id = "test-task"
        db.get_config.return_value = {
            "enabled": True,
            "base_url": "https://sub2api.example",
            "admin_token": "admin-key",
            "proxy_id": 9,
            "load_factor": 80,
            "model_whitelist": ["gpt-5.6-sol"],
        }
        retry = Mock(status_code=503, text="temporary")
        success = Mock(status_code=200, text='{"success":1,"failed":0}')
        success.json.return_value = {"success": 1, "failed": 0, "results": []}
        session = {"access_token": "at", "refresh_token": "rt"}

        with patch.object(worker.requests, "post", side_effect=[retry, success]) as post:
            worker._import_sub2api(db, "user@example.com", 7, session)

        self.assertEqual(post.call_count, 2)
        first = post.call_args_list[0].kwargs
        second = post.call_args_list[1].kwargs
        self.assertEqual(first["headers"]["Idempotency-Key"], second["headers"]["Idempotency-Key"])
        account = second["json"]["accounts"][0]
        self.assertEqual(account["proxy_id"], 9)
        self.assertEqual(account["load_factor"], 80)
        self.assertEqual(account["credentials"]["model_mapping"], {"gpt-5.6-sol": "gpt-5.6-sol"})

        ambiguous = Mock(status_code=200, text='{"message":"accepted"}')
        ambiguous.json.return_value = {"message": "accepted"}
        with patch.object(worker.requests, "post", return_value=ambiguous):
            with self.assertRaisesRegex(RuntimeError, "未确认成功"):
                worker._import_sub2api(db, "user@example.com", 7, session)

        nested = Mock(status_code=200, text='{"results":[]}')
        nested.json.return_value = {
            "results": [{"status": "created", "account": {"id": "remote-9", "email": "user@example.com"}}]
        }
        with patch.object(worker.requests, "post", return_value=nested):
            worker._import_sub2api(db, "user@example.com", 7, session)
        self.assertEqual(db.set_account_sub2api_status.call_args.args, ("user@example.com", "imported", "remote-9"))

        with self.assertRaisesRegex(RuntimeError, "Access Token"):
            worker._import_sub2api(db, "user@example.com", 7, {"refresh_token": "rt"})


if __name__ == "__main__":
    unittest.main()
