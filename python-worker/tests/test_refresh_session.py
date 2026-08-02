from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from sunny_core import worker
from sunny_core.db import SunnyDB, SunnyTaskCancelled


class FakeRefreshDB:
    def __init__(self, *, refresh_token: str = "", mailbox_status: str = "已注册") -> None:
        self.refresh_token = refresh_token
        self.mailbox_status = mailbox_status
        self.events: list[str] = []
        self.event_details: list[dict] = []
        self.sessions: list[dict] = []
        self.marked_status = ""
        self.renewal_failure = ""
        self.deactivated_error = ""

    def fetch_accounts(self, _ids=None):
        return [{"id": 7, "email": "registered@example.com", "openai_rt": self.refresh_token}]

    def fetch_session_by_email(self, _email):
        return {"refresh_token": self.refresh_token}

    def fetch_mailbox_by_email(self, _email):
        return {"id": 11, "email": "registered@example.com", "status": self.mailbox_status}

    def ensure_not_cancelled(self):
        return None

    def event(self, message, *_args, **_kwargs):
        self.events.append(message)
        detail = _kwargs.get("detail")
        if isinstance(detail, dict):
            self.event_details.append(detail)

    def update_task(self, **_kwargs):
        return None

    def upsert_session(self, _email, _account_id, session, _raw_line=""):
        self.sessions.append(session)

    def upsert_account(self, *_args, **_kwargs):
        return 7

    def mark_mailbox_by_email(self, *_args, **_kwargs):
        self.marked_status = str(_args[1])
        return None

    def mark_access_token_renewal_failed(self, _email, error=""):
        self.renewal_failure = str(error)

    def mark_account_deactivated(self, _email, error=""):
        self.deactivated_error = str(error)


class RefreshSessionTests(unittest.TestCase):
    def test_cancelled_renewal_stops_without_recording_failure(self):
        db = FakeRefreshDB()
        with patch.object(worker, "_run_one", side_effect=SunnyTaskCancelled("Task cancelled by user")):
            with self.assertRaises(SunnyTaskCancelled):
                worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(db.renewal_failure, "")

    def test_missing_refresh_token_reuses_protocol_native_headless_login(self):
        db = FakeRefreshDB()
        with patch.object(worker, "_run_one", return_value=(True, {"has_access_token": True, "has_refresh_token": False})) as run_one:
            ok, errors, items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["refresh_method"], "headless_login")
        payload = run_one.call_args.args[2]
        self.assertEqual(payload["execution_mode"], "protocol")
        self.assertEqual(payload["protocol_challenge_strategy"], "native_headless")
        self.assertEqual(payload["registration_stage"], "register_only")
        renewal = [item for item in db.event_details if item.get("progress_type") == "access_token_renewal"]
        self.assertEqual((renewal[0]["current"], renewal[0]["total"]), (1, 7))
        self.assertEqual((renewal[-1]["current"], renewal[-1]["total"], renewal[-1]["state"]), (9, 9, "succeeded"))

    def test_refresh_token_is_used_before_browser_fallback(self):
        db = FakeRefreshDB(refresh_token="rt_test")
        token = {"access_token": "at_new", "refresh_token": "rt_new", "expires_at": 1893456000}
        with (
            patch.object(worker, "refresh_openai_access_token", return_value=token),
            patch.object(worker, "_run_one") as run_one,
        ):
            ok, errors, items = worker._refresh_sessions(db, {"account_ids": [7], "proxy_enabled": False})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["refresh_method"], "refresh_token")
        self.assertEqual(db.sessions[0]["expires_at"], 1893456000)
        run_one.assert_not_called()
        renewal = [item for item in db.event_details if item.get("progress_type") == "access_token_renewal"]
        self.assertEqual([item["current"] for item in renewal], list(range(1, 8)))
        self.assertEqual(renewal[-1]["state"], "succeeded")

    def test_refresh_token_does_not_downgrade_reverse_proxy_status(self):
        db = FakeRefreshDB(refresh_token="rt_test", mailbox_status="已反代")
        token = {"access_token": "at_new", "refresh_token": "rt_new"}
        with patch.object(worker, "refresh_openai_access_token", return_value=token):
            ok, errors, _items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(db.marked_status, "已反代")

    def test_failed_renewal_is_persisted_for_account_status_display(self):
        db = FakeRefreshDB()
        with patch.object(worker, "_run_one", return_value=(False, "login failed")):
            ok, errors, _items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 0)
        self.assertIn("login failed", errors[0])
        self.assertIn("login failed", db.renewal_failure)

    def test_account_deactivated_is_banned_without_retry(self):
        db = FakeRefreshDB()
        deactivated = (
            'EmailOtpValidate failed: HTTP 403 sentinel=yes {"error": {'
            '"message": "You do not have an account because it has been deleted or deactivated.", '
            '"code": "account_deactivated"}}'
        )
        with patch.object(worker, "_run_one", return_value=(False, deactivated)) as run_one:
            ok, errors, _items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 0)
        self.assertIn("account_deactivated", errors[0])
        self.assertIn("account_deactivated", db.deactivated_error)
        self.assertEqual(db.renewal_failure, "")
        self.assertEqual(run_one.call_count, 1)
        self.assertFalse(worker._is_otp_security_context_failure(deactivated))

    def test_otp_403_retries_once_with_fresh_headless_context(self):
        db = FakeRefreshDB()
        otp_error = (
            "邮箱验证码已由页面提交，但注册状态未推进。关键请求："
            "REQ POST https://auth.openai.com/api/accounts/email-otp/validate | "
            "RESP 403 application/json"
        )
        with (
            patch.object(
                worker,
                "_run_one",
                side_effect=[
                    (False, otp_error),
                    (True, {"has_access_token": True, "has_refresh_token": False}),
                ],
            ) as run_one,
            patch.object(worker.time, "sleep", return_value=None) as sleep_mock,
        ):
            ok, errors, items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["refresh_method"], "headless_login")
        self.assertEqual(run_one.call_count, 2)
        retry_payload = run_one.call_args_list[1].args[2]
        self.assertEqual(retry_payload["execution_mode"], "background")
        self.assertTrue(retry_payload["renewal_retry_fresh_context"])
        self.assertTrue(any("新的隔离无痕后台浏览器上下文" in message for message in db.events))
        self.assertEqual(sleep_mock.call_count, 15)

    def test_protocol_failure_falls_back_to_fresh_background_login(self):
        db = FakeRefreshDB()
        with (
            patch.object(
                worker,
                "_run_one",
                side_effect=[
                    (False, "protocol request failed"),
                    (True, {"has_access_token": True, "has_refresh_token": False}),
                ],
            ) as run_one,
            patch.object(worker.time, "sleep", return_value=None) as sleep_mock,
        ):
            ok, errors, items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["refresh_method"], "headless_login")
        self.assertEqual(run_one.call_args_list[0].args[2]["execution_mode"], "protocol")
        self.assertEqual(run_one.call_args_list[1].args[2]["execution_mode"], "background")
        self.assertTrue(run_one.call_args_list[1].args[2]["renewal_retry_fresh_context"])
        self.assertEqual(sleep_mock.call_count, 2)

    def test_failed_renewal_does_not_duplicate_email_prefix(self):
        db = FakeRefreshDB()
        with patch.object(worker, "_run_one", return_value=(False, "[registered@example.com] login failed")):
            _ok, errors, _items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(errors, ["[registered@example.com] login failed"])

    def test_wrong_otp_does_not_retry_security_context(self):
        self.assertFalse(worker._is_otp_security_context_failure("邮箱验证码被 OpenAI 拒绝；验证码错误"))


class AccountDeactivatedPersistenceTests(unittest.TestCase):
    def test_marks_mailbox_account_and_session_atomically(self):
        db = SunnyDB.__new__(SunnyDB)
        db.task_id = "test"
        db.conn = sqlite3.connect(":memory:")
        db.conn.row_factory = sqlite3.Row
        db.conn.execute("create table sunny_mailboxes(email text, status text, last_error text, last_health_checked_at text, status_changed_at text, updated_at text)")
        db.conn.execute("create table sunny_accounts(email text, status text, last_error text, last_health_checked_at text, status_changed_at text, updated_at text)")
        db.conn.execute("create table sunny_sessions(email text, access_token_status text, access_token_checked_at text, access_token_error text, updated_at text)")
        db.conn.execute("insert into sunny_mailboxes(email,status) values('user@example.com','已注册')")
        db.conn.execute("insert into sunny_accounts(email,status) values('user@example.com','registered')")
        db.conn.execute("insert into sunny_sessions(email,access_token_status) values('user@example.com','renewal_failed')")

        db.mark_account_deactivated("user@example.com", "account_deactivated")

        mailbox = db.conn.execute("select * from sunny_mailboxes").fetchone()
        account = db.conn.execute("select * from sunny_accounts").fetchone()
        session = db.conn.execute("select * from sunny_sessions").fetchone()
        self.assertEqual(mailbox["status"], "已封禁")
        self.assertEqual(account["status"], "banned")
        self.assertEqual(session["access_token_status"], "invalid")
        self.assertTrue(mailbox["last_health_checked_at"])
        self.assertEqual(mailbox["last_health_checked_at"], account["last_health_checked_at"])
        self.assertIn("account_deactivated", session["access_token_error"])
        db.close()


class AcquireRefreshTokenTests(unittest.TestCase):
    def test_existing_refresh_token_returns_without_login(self):
        db = FakeRefreshDB(refresh_token="rt_existing")
        with patch.object(worker, "_run_one") as run_one:
            ok, errors, items = worker._acquire_refresh_tokens(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["acquire_method"], "existing")
        run_one.assert_not_called()

    def test_missing_refresh_token_runs_background_codex_oauth(self):
        db = FakeRefreshDB()
        with patch.object(worker, "_run_one", return_value=(True, {"has_refresh_token": True})) as run_one:
            ok, errors, items = worker._acquire_refresh_tokens(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["acquire_method"], "codex_oauth")
        self.assertEqual(run_one.call_args.args[1], "sunny_acquire_rt")
        payload = run_one.call_args.args[2]
        self.assertEqual(payload["execution_mode"], "background")
        self.assertEqual(payload["registration_stage"], worker.CODEX_PHONE_BIND)

    def test_missing_refresh_token_reports_clear_failure(self):
        db = FakeRefreshDB()
        result = {"has_refresh_token": False, "stage_error": "OAuth phone verification required"}
        with patch.object(worker, "_run_one", return_value=(True, result)):
            ok, errors, items = worker._acquire_refresh_tokens(db, {"account_ids": [7]})

        self.assertEqual(ok, 0)
        self.assertEqual(items, [])
        self.assertIn("无法获取该账户RT", errors[0])
        self.assertIn("OAuth phone verification required", errors[0])


if __name__ == "__main__":
    unittest.main()
