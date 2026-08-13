from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sunny_core.db import SunnyDB


def _create_database(path: Path, *, structured: bool) -> None:
    conn = sqlite3.connect(path)
    extra = """
        scope text, subject_type text, subject_key text, email text,
        account_id integer, mailbox_id integer, module text, action text, operation_id text,
    """ if structured else ""
    conn.executescript(f"""
        create table tasks (id text primary key, type text, status text, payload_json text, result_json text,
            progress_current integer default 0, progress_total integer default 0, success_count integer default 0,
            error_count integer default 0, error text default '', updated_at text);
        create table task_events (id integer primary key autoincrement, task_id text, type text, level text,
            message text, {extra} detail_json text, created_at text);
    """)
    conn.execute("insert into tasks(id,type,status,payload_json,result_json) values(?,?,?,?,?)", ("task-1", "test", "running", "{}", "{}"))
    conn.commit()
    conn.close()


class StructuredTaskEventTests(unittest.TestCase):
    def test_account_event_writes_queryable_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "events.db"
            _create_database(database, structured=True)
            with patch("sunny_core.db.db_path", return_value=str(database)):
                db = SunnyDB("task-1", ensure_schema=False)
                try:
                    db.account_event(
                        "User@Example.com", "session", "access_token.renewed", "[User@Example.com] [Session] renewed",
                        account_id=7, operation_id="renew-1", detail={"access_token": "secret-token"},
                    )
                    row = db.conn.execute("select * from task_events").fetchone()
                finally:
                    db.close()
            self.assertEqual(row["email"], "User@Example.com")
            self.assertEqual(row["subject_key"], "user@example.com")
            self.assertEqual(row["module"], "session")
            self.assertEqual(row["action"], "access_token.renewed")
            self.assertEqual(row["operation_id"], "renew-1")
            self.assertEqual(json.loads(row["detail_json"])["access_token"], "[REDACTED]")

    def test_event_keeps_legacy_table_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "legacy-events.db"
            _create_database(database, structured=False)
            with patch("sunny_core.db.db_path", return_value=str(database)):
                db = SunnyDB("task-1", ensure_schema=False)
                try:
                    db.event("[legacy@example.com] [邮箱] waiting")
                    row = db.conn.execute("select detail_json from task_events").fetchone()
                finally:
                    db.close()
            detail = json.loads(row["detail_json"])
            self.assertEqual(detail["email"], "legacy@example.com")
            self.assertEqual(detail["module"], "mailbox")
            self.assertEqual(detail["scope"], "account")

    def test_event_sanitizes_message_and_generates_operation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "sanitized-events.db"
            _create_database(database, structured=True)
            with patch("sunny_core.db.db_path", return_value=str(database)):
                db = SunnyDB("task-1", ensure_schema=False)
                try:
                    db.event("[safe@example.com] [邮箱] received OTP 123456 using Bearer abcdefghijklmnopqrstuvwxyz")
                    row = db.conn.execute("select * from task_events").fetchone()
                finally:
                    db.close()
            self.assertNotIn("123456", row["message"])
            self.assertNotIn("abcdefghijklmnop", row["message"])
            self.assertEqual(row["operation_id"], "task-1:safe@example.com:mailbox")


if __name__ == "__main__":
    unittest.main()
