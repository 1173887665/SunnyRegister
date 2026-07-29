from __future__ import annotations

import unittest
from unittest.mock import patch

from sunny_core import worker


class FakeRefreshDB:
    def __init__(self, *, refresh_token: str = "", mailbox_status: str = "已注册") -> None:
        self.refresh_token = refresh_token
        self.mailbox_status = mailbox_status
        self.events: list[str] = []
        self.event_details: list[dict] = []
        self.sessions: list[dict] = []
        self.marked_status = ""
        self.renewal_failure = ""

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


class RefreshSessionTests(unittest.TestCase):
    def test_missing_refresh_token_falls_back_to_background_login(self):
        db = FakeRefreshDB()
        with patch.object(worker, "_run_one", return_value=(True, {"has_access_token": True, "has_refresh_token": False})) as run_one:
            ok, errors, items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(items[0]["refresh_method"], "headless_login")
        payload = run_one.call_args.args[2]
        self.assertEqual(payload["execution_mode"], "background")
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
