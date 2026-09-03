from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sunny_core.db import SunnyDB
from worker import _task_activity_signature, _terminate_process_tree


class TaskCancellationTests(unittest.TestCase):
    def test_watchdog_activity_ignores_heartbeat_updated_at(self) -> None:
        task = {"progress_current": 3, "status": "running", "updated_at": "2026-09-03 15:00:00"}
        first = _task_activity_signature(task, "2026-09-03 15:00:00")
        task["updated_at"] = "2026-09-03 15:00:15"
        second = _task_activity_signature(task, "2026-09-03 15:00:00")
        self.assertEqual(first, second)

    def test_force_stop_terminates_task_process(self) -> None:
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        try:
            _terminate_process_tree(process)
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()

    def test_cancel_preserves_completed_mailbox_and_fails_unfinished_mailbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "cancel.db"
            conn = sqlite3.connect(database)
            conn.executescript(
                """
                create table tasks (
                    id text primary key,
                    type text,
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
                    sub2api_status text default '',
                    metadata_json text
                );
                """
            )
            task_id = "task-cancel-test"
            conn.execute(
                "insert into tasks(id,type,status,payload_json,result_json,progress_total) values(?,?,?,?,?,?)",
                (task_id, "sunny_register", "cancel_requested", json.dumps({"mailbox_ids": [1, 2, 3]}), "{}", 3),
            )
            conn.executemany(
                "insert into sunny_mailboxes(id,status) values(?,?)",
                [(1, "已注册"), (2, "注册中"), (3, "已接码")],
            )
            conn.execute(
                "insert into sunny_accounts(id,mailbox_id,status,metadata_json) values(?,?,?,?)",
                (1, 1, "registered", json.dumps({"task_id": task_id})),
            )
            conn.execute(
                "insert into sunny_accounts(id,mailbox_id,status,sub2api_status,metadata_json) values(?,?,?,?,?)",
                (2, 3, "phone_bound", "imported", json.dumps({"task_id": task_id, "completed_status": "已接码"})),
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

            self.assertEqual(summary["completed_mailbox_ids"], [1, 3])
            self.assertEqual(summary["failed_mailbox_ids"], [2])
            self.assertEqual(rows[0]["status"], "已注册")
            self.assertEqual(rows[0]["last_error"], "")
            self.assertEqual(rows[1]["status"], "已取消")
            self.assertIn("停止", rows[1]["last_error"])
            self.assertEqual(rows[2]["status"], "已反代")
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(task["success_count"], 2)
            self.assertEqual(task["error_count"], 1)

    def test_cancel_renewal_preserves_mailbox_registration_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "cancel-renewal.db"
            conn = sqlite3.connect(database)
            conn.executescript(
                """
                create table tasks (
                    id text primary key,
                    type text,
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
                """
            )
            task_id = "task-renewal-cancel-test"
            conn.execute(
                "insert into tasks(id,type,status,payload_json,result_json,progress_current,progress_total,success_count) values(?,?,?,?,?,?,?,?)",
                (task_id, "sunny_refresh_session", "cancel_requested", json.dumps({"account_ids": [7, 8]}), "{}", 1, 2, 1),
            )
            conn.executemany(
                "insert into sunny_mailboxes(id,status,last_error) values(?,?,?)",
                [(1, "已注册", ""), (2, "已接码", "")],
            )
            conn.commit()
            conn.close()

            with patch("sunny_core.db.db_path", return_value=str(database)):
                db = SunnyDB(task_id, ensure_schema=False)
                try:
                    summary = db.mark_cancelled("用户已停止 AT 续期任务")
                    rows = db.conn.execute("select id,status,last_error from sunny_mailboxes order by id").fetchall()
                    task = db.task()
                finally:
                    db.close()

            self.assertEqual(summary["completed"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual([(row["status"], row["last_error"]) for row in rows], [("已注册", ""), ("已接码", "")])
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(task["progress_current"], 1)
            self.assertEqual(task["success_count"], 1)
            self.assertEqual(task["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
