from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql


TABLES = [
    "configs", "accounts", "account_overviews", "account_credentials",
    "provider_accounts", "provider_resources", "provider_definitions", "provider_settings",
    "platform_capability_overrides", "task_logs", "tasks", "task_events", "proxies",
    "sms_pool_blacklist", "sunny_mailbox_groups", "sunny_mailboxes", "sunny_phones",
    "sunny_proxies", "sunny_accounts", "sunny_sessions", "sunny_configs",
    "sunny_sms_provider_options", "sunny_sms_provider_numbers", "audit_logs",
    "audit_settings", "audit_export_jobs",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate SunnyRegister SQLite data to PostgreSQL")
    parser.add_argument("--sqlite", required=True, help="Path to the source SQLite database")
    parser.add_argument("--postgres", default=os.getenv("DATABASE_URL", ""), help="Target PostgreSQL URL")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--allow-non-empty", action="store_true", help="Upsert into a non-empty target")
    return parser.parse_args()


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def postgres_columns(conn: psycopg.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(
        "select column_name, data_type from information_schema.columns where table_schema='public' and table_name=%s order by ordinal_position",
        (table,),
    ).fetchall()
    return {str(name): str(kind) for name, kind in rows}


def normalize(value: Any, data_type: str) -> Any:
    if value is None:
        return None
    if data_type == "boolean":
        if isinstance(value, str):
            return value.strip().casefold() not in {"", "0", "false", "f", "no", "n", "off"}
        return bool(value)
    if data_type.startswith("timestamp") and isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return parsed
        except ValueError:
            return value
    return value


def target_has_data(conn: psycopg.Connection) -> list[str]:
    populated: list[str] = []
    for table in TABLES:
        if not postgres_columns(conn, table):
            continue
        if conn.execute(sql.SQL("select exists(select 1 from {} limit 1)").format(sql.Identifier(table))).fetchone()[0]:
            populated.append(table)
    return populated


def reset_sequence(conn: psycopg.Connection, table: str, columns: list[str]) -> None:
    if "id" not in columns:
        return
    sequence = conn.execute("select pg_get_serial_sequence(%s, 'id')", (f"public.{table}",)).fetchone()[0]
    if sequence:
        conn.execute(
            sql.SQL("select setval(%s, coalesce((select max(id) from {}), 1), (select count(*) > 0 from {}))").format(
                sql.Identifier(table), sql.Identifier(table)
            ),
            (sequence,),
        )


def migrate_table(source: sqlite3.Connection, target: psycopg.Connection, table: str, batch_size: int, upsert: bool) -> int:
    source_cols = sqlite_columns(source, table)
    target_cols = postgres_columns(target, table)
    columns = [name for name in source_cols if name in target_cols]
    if not columns:
        return 0
    quoted = ", ".join(f'"{name}"' for name in columns)
    query = f'SELECT {quoted} FROM "{table}"'
    conflict = sql.SQL(" ON CONFLICT DO NOTHING") if upsert else sql.SQL("")
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    ) + conflict
    total = 0
    cursor = source.execute(query)
    while rows := cursor.fetchmany(max(1, batch_size)):
        values = [tuple(normalize(row[index], target_cols[name]) for index, name in enumerate(columns)) for row in rows]
        with target.cursor() as target_cursor:
            target_cursor.executemany(insert, values)
        total += len(values)
    reset_sequence(target, table, columns)
    return total


def main() -> None:
    args = parse_args()
    source_path = Path(args.sqlite).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"SQLite source does not exist: {source_path}")
    if not args.postgres.startswith(("postgres://", "postgresql://")):
        raise SystemExit("--postgres or DATABASE_URL must be a PostgreSQL URL")
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    try:
        with psycopg.connect(args.postgres) as target:
            populated = target_has_data(target)
            if populated and not args.allow_non_empty:
                raise SystemExit("PostgreSQL target is not empty: " + ", ".join(populated))
            counts: dict[str, int] = {}
            for table in TABLES:
                if not sqlite_columns(source, table) or not postgres_columns(target, table):
                    continue
                counts[table] = migrate_table(source, target, table, args.batch_size, args.allow_non_empty)
                print(f"{table}: {counts[table]}")
            target.execute("analyze")
        print(f"Migration completed: {sum(counts.values())} rows across {len(counts)} tables")
    finally:
        source.close()


if __name__ == "__main__":
    main()
