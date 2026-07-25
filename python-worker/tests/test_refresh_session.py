from __future__ import annotations

import unittest
from unittest.mock import patch

from sunny_core import worker


class FakeRefreshDB:
    def __init__(self, *, refresh_token: str = "", mailbox_status: str = "已注册") -> None:
        self.refresh_token = refresh_token
        self.mailbox_status = mailbox_status
        self.events: list[str] = []
        self.sessions: list[dict] = []
        self.marked_status = ""

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

    def update_task(self, **_kwargs):
        return None

    def upsert_session(self, _email, _account_id, session, _raw_line=""):
        self.sessions.append(session)

    def upsert_account(self, *_args, **_kwargs):
        return 7

    def mark_mailbox_by_email(self, *_args, **_kwargs):
        self.marked_status = str(_args[1])
        return None


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

    def test_refresh_token_does_not_downgrade_reverse_proxy_status(self):
        db = FakeRefreshDB(refresh_token="rt_test", mailbox_status="已反代")
        token = {"access_token": "at_new", "refresh_token": "rt_new"}
        with patch.object(worker, "refresh_openai_access_token", return_value=token):
            ok, errors, _items = worker._refresh_sessions(db, {"account_ids": [7]})

        self.assertEqual(ok, 1)
        self.assertEqual(errors, [])
        self.assertEqual(db.marked_status, "已反代")


if __name__ == "__main__":
    unittest.main()
