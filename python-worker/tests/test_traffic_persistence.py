from __future__ import annotations

import sqlite3

from sunny_core.db import SunnyDB


def test_record_proxy_traffic_accumulates_history_and_freezes_registration(tmp_path, monkeypatch) -> None:
    database = tmp_path / "traffic.sqlite3"
    conn = sqlite3.connect(database)
    conn.execute(
        """
        create table sunny_mailboxes (
            id integer primary key,
            email text,
            chatgpt_register_traffic_bytes integer default 0,
            proxy_traffic_bytes integer default 0,
            registration_traffic_finalized_at datetime,
            updated_at datetime
        )
        """
    )
    conn.execute("insert into sunny_mailboxes(id, email) values (1, 'a@example.com')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("ACCOUNT_MANAGER_DATABASE_URL", str(database))
    db = SunnyDB("traffic-test", ensure_schema=False)
    try:
        db.record_proxy_traffic("a@example.com", 1, 1045, registration_attempt=True)
        row = db.conn.execute(
            "select chatgpt_register_traffic_bytes, proxy_traffic_bytes, registration_traffic_finalized_at from sunny_mailboxes where id=1"
        ).fetchone()
        assert row["chatgpt_register_traffic_bytes"] == 1045
        assert row["proxy_traffic_bytes"] == 1045
        assert row["registration_traffic_finalized_at"] is None

        db.record_proxy_traffic("a@example.com", 1, 10, registration_attempt=True, registration_succeeded=True)
        row = db.conn.execute(
            "select chatgpt_register_traffic_bytes, proxy_traffic_bytes, registration_traffic_finalized_at from sunny_mailboxes where id=1"
        ).fetchone()
        assert row["chatgpt_register_traffic_bytes"] == 1055
        assert row["proxy_traffic_bytes"] == 1055
        assert row["registration_traffic_finalized_at"]

        db.record_proxy_traffic("a@example.com", 1, 500, registration_attempt=True, registration_succeeded=True)
        row = db.conn.execute(
            "select chatgpt_register_traffic_bytes, proxy_traffic_bytes from sunny_mailboxes where id=1"
        ).fetchone()
        assert row["chatgpt_register_traffic_bytes"] == 1055
        assert row["proxy_traffic_bytes"] == 1555
    finally:
        db.close()
