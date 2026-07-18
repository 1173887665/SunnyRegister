from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sunny_core.db import SunnyDB


class TaskCancellationTests(unittest.TestCase):
    def test_cancel_preserves_completed_mailbox_and_fails_unfinished_mailbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "cancel.db"
            conn = sqlite3.connect(database)
            conn.executescript(
                """
                create table tasks (
                    id text primary key,
                    status text,
                    payload_json text,
                    result_json text,
                    progress_current integer default 0,
                    progress_total integer default 0,
                    success_count integer default 0,
                    error_count integer default 0,
                    error text default '',
                    finished_at text,
                    updated_at text
                );
                create table task_events (
                    id integer primary key autoincrement,
                    task_id text,
                    type text,
                    level text,
                    message text,
                    detail_json text,
                    created_at text
                );
                create table sunny_mailboxes (
                    id integer primary key,
                    status text,
                    last_error text default '',
                    updated_at text
                );
                create table sunny_accounts (
                    id integer primary key,
                    mailbox_id integer,
                    status text,
                    metadata_json text
                );
                """
            )
            task_id = "task-cancel-test"
            conn.execute(
                "insert into tasks(id,status,payload_json,result_json,progress_total) values(?,?,?,?,?)",
                (task_id, "cancel_requested", json.dumps({"mailbox_ids": [1, 2]}), "{}", 2),
            )
            conn.executemany(
                "insert into sunny_mailboxes(id,status) values(?,?)",
                [(1, "已注册"), (2, "注册中")],
            )
            conn.execute(
                "insert into sunny_accounts(id,mailbox_id,status,metadata_json) values(?,?,?,?)",
                (1, 1, "registered", json.dumps({"task_id": task_id})),
            )
            conn.commit()
            conn.close()

            with patch("sunny_core.db.db_path", return_value=str(database)):
                db = SunnyDB(task_id, ensure_schema=False)
                try:
                    summary = db.mark_cancelled()
                    rows = db.conn.execute("select id,status,last_error from sunny_mailboxes order by id").fetchall()
                    task = db.task()
                finally:
                    db.close()

            self.assertEqual(summary["completed_mailbox_ids"], [1])
            self.assertEqual(summary["failed_mailbox_ids"], [2])
            self.assertEqual(rows[0]["status"], "已注册")
            self.assertEqual(rows[0]["last_error"], "")
            self.assertEqual(rows[1]["status"], "失败")
            self.assertIn("停止", rows[1]["last_error"])
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(task["success_count"], 1)
            self.assertEqual(task["error_count"], 1)


if __name__ == "__main__":
    unittest.main()
