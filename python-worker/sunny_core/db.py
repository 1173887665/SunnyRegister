from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def db_path() -> str:
    raw = os.getenv("ACCOUNT_MANAGER_DATABASE_URL") or os.getenv("ACCOUNT_MANAGER_DB") or "sqlite:///data/account_manager.db"
    raw = raw.strip()
    if raw.startswith("sqlite:///"):
        return raw[10:]
    if raw.startswith("sqlite://"):
        return raw[9:]
    return raw


def now_sql() -> str:
    tz_name = os.getenv("SUNNY_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
    try:
        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return time.strftime("%Y-%m-%d %H:%M:%S")


def app_timezone() -> ZoneInfo:
    tz_name = os.getenv("SUNNY_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


class SunnyTaskCancelled(RuntimeError):
    """Raised when the Go backend marks a SunnyRegister task as cancelled."""


_SENSITIVE_EVENT_KEYS = {
    "access_token", "refresh_token", "id_token", "openai_rt", "session_json",
    "password", "secret", "api_key", "admin_token", "authorization", "otp", "code",
}


def _sanitize_event_detail(value: Any, key: str = "") -> Any:
    normalized = key.lower().strip()
    if normalized in _SENSITIVE_EVENT_KEYS or normalized.endswith(("_password", "_secret", "_token", "_api_key")):
        return "[REDACTED]" if value not in (None, "") else value
    if isinstance(value, dict):
        return {str(k): _sanitize_event_detail(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_event_detail(item, key) for item in value]
    return value


class SunnyDB:
    def __init__(self, task_id: str, *, ensure_schema: bool = True):
        self.task_id = task_id
        self.conn = sqlite3.connect(db_path(), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma busy_timeout=30000")
        self.conn.execute("pragma foreign_keys=on")
        if ensure_schema:
            self.ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def ensure_schema(self) -> None:
        """Keep the Python worker compatible with databases created by older builds."""
        wanted = {
            "sunny_accounts": {
                "mailbox_id": "integer DEFAULT 0",
                "group_name": "text DEFAULT ''",
                "status": "text DEFAULT 'pending'",
                "account_type": "text DEFAULT 'free'",
                "openai_rt": "text DEFAULT ''",
                "access_token": "text DEFAULT ''",
                "phone_number": "text DEFAULT ''",
                "sub2api_status": "text DEFAULT ''",
                "sub2api_id": "text DEFAULT ''",
                "last_error": "text DEFAULT ''",
                "metadata_json": "text DEFAULT '{}'",
                "last_health_checked_at": "datetime",
                "status_changed_at": "datetime",
                "created_at": "datetime",
                "updated_at": "datetime",
            },
            "sunny_mailboxes": {
                "openai_rt": "text DEFAULT ''",
                "registered_at": "datetime",
                "last_error": "text DEFAULT ''",
                "last_health_checked_at": "datetime",
                "status_changed_at": "datetime",
            },
            "sunny_sessions": {
                "refresh_token": "text DEFAULT ''",
                "id_token": "text DEFAULT ''",
                "session_json": "text DEFAULT '{}'",
                "storage_state_json": "text DEFAULT '{}'",
                "raw_mailbox_line": "text DEFAULT ''",
                "last_refresh_at": "datetime",
            },
        }
        for table, columns in wanted.items():
            try:
                existing = {str(row["name"]) for row in self.conn.execute(f"pragma table_info({table})").fetchall()}
            except Exception:
                existing = set()
            if not existing:
                continue
            for name, ddl in columns.items():
                if name in existing:
                    continue
                self.conn.execute(f"alter table {table} add column {name} {ddl}")
            refreshed = {str(row["name"]) for row in self.conn.execute(f"pragma table_info({table})").fetchall()}
            if table in {"sunny_accounts", "sunny_mailboxes"} and "open_airt" in refreshed and "openai_rt" in refreshed:
                self.conn.execute(f"update {table} set openai_rt=open_airt where coalesce(openai_rt,'')='' and coalesce(open_airt,'')<>''")

        for table in ("sunny_accounts", "sunny_mailboxes"):
            columns = {str(row["name"]) for row in self.conn.execute(f"pragma table_info({table})").fetchall()}
            if "status_changed_at" not in columns:
                continue
            self.conn.execute(f"update {table} set status_changed_at=updated_at where status_changed_at is null")
            self.conn.execute(
                f"""create trigger if not exists trg_{table}_status_changed
                after update of status on {table}
                when old.status is not new.status
                begin
                    update {table}
                    set status_changed_at=case
                        when new.updated_at is not old.updated_at then new.updated_at
                        else datetime('now','localtime')
                    end
                    where id=new.id;
                end"""
            )
            self.conn.execute(
                f"""create trigger if not exists trg_{table}_status_created
                after insert on {table}
                when new.status_changed_at is null
                begin
                    update {table}
                    set status_changed_at=coalesce(new.created_at,new.updated_at,datetime('now','localtime'))
                    where id=new.id;
                end"""
            )
        self.conn.execute("update sunny_mailboxes set status='已接码' where status='PLUS试用中'")
        self.conn.execute("update sunny_accounts set status='phone_bound' where status='PLUS试用中'")
        self.conn.execute(
            """
            create table if not exists sunny_sms_provider_numbers (
                id integer primary key autoincrement,
                provider text not null,
                phone_number text not null,
                country text default '',
                service text default '',
                pool text default '',
                last_order_id text default '',
                token text default '',
                status text default 'available',
                success_count integer default 0,
                max_success integer default 3,
                cooldown_until datetime,
                last_error text default '',
                last_used_at datetime,
                created_at datetime,
                updated_at datetime
            )
            """
        )
        self.conn.execute(
            "create unique index if not exists idx_sunny_sms_provider_number on sunny_sms_provider_numbers(provider, phone_number, country, service)"
        )
        self.conn.commit()

    def task(self) -> dict[str, Any]:
        row = self.conn.execute("select * from tasks where id=?", (self.task_id,)).fetchone()
        if not row:
            raise RuntimeError(f"task not found: {self.task_id}")
        return dict(row)

    def event(self, message: str, level: str = "info", typ: str = "log", detail: dict[str, Any] | None = None) -> None:
        created_at = now_sql()
        event_detail = _sanitize_event_detail(dict(detail or {}))
        event_detail.setdefault("local_created_at", created_at)
        self.conn.execute(
            "insert into task_events(task_id,type,level,message,detail_json,created_at) values(?,?,?,?,?,?)",
            (self.task_id, typ, level, str(message), json.dumps(event_detail, ensure_ascii=False), created_at),
        )
        self.conn.commit()

    def update_task(self, **fields: Any) -> None:
        if not fields:
            return
        if "status" in fields and str(fields.get("status") or "") not in {"cancelled", "interrupted"}:
            row = self.conn.execute("select status from tasks where id=?", (self.task_id,)).fetchone()
            current = str(row["status"] if row else "")
            if current in {"cancel_requested", "cancelled", "interrupted"}:
                fields["status"] = "cancelled"
                fields.setdefault("error", "用户已中断注册任务")
                fields.setdefault("finished_at", now_sql())
        fields["updated_at"] = now_sql()
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"update tasks set {sets} where id=?", [*fields.values(), self.task_id])
        self.conn.commit()

    def task_status(self) -> str:
        row = self.conn.execute("select status from tasks where id=?", (self.task_id,)).fetchone()
        return str(row["status"] if row else "")

    def cancel_requested(self) -> bool:
        return self.task_status() in {"cancel_requested", "cancelled", "interrupted"}

    def ensure_not_cancelled(self) -> None:
        if self.cancel_requested():
            raise SunnyTaskCancelled("Task cancelled by user")

    def mark_cancelled(self, message: str = "用户已停止注册任务") -> dict[str, Any]:
        current = self.task_status()
        summary = self.fail_unfinished_mailboxes(message)
        task = self.task()
        try:
            result = json.loads(task.get("result_json") or "{}")
            if not isinstance(result, dict):
                result = {}
        except Exception:
            result = {}
        result.update({"cancelled": True, **summary})
        self.update_task(
            status="cancelled",
            error=message,
            progress_current=summary["completed"] + summary["failed"],
            success_count=summary["completed"],
            error_count=summary["failed"],
            result_json=json.dumps(result, ensure_ascii=False),
            finished_at=now_sql(),
        )
        if current not in {"cancelled", "interrupted"}:
            self.event(
                f"{message}；已完成 {summary['completed']} 个，未完成并标记失败 {summary['failed']} 个",
                "warning",
                detail={"scope": "global", "cancelled": True, **summary},
            )
        return summary

    def fail_unfinished_mailboxes(self, reason: str = "任务已由用户停止，当前邮箱未完成本次注册流程") -> dict[str, Any]:
        """Fail selected mailboxes that did not complete successfully in this task."""
        task = self.task()
        try:
            payload = json.loads(task.get("payload_json") or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

        mailbox_ids: list[int] = []
        for raw in payload.get("mailbox_ids") or []:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in mailbox_ids:
                mailbox_ids.append(value)
        if not mailbox_ids:
            account_ids: list[int] = []
            for raw in payload.get("account_ids") or []:
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    account_ids.append(value)
            if account_ids:
                marks = ",".join("?" for _ in account_ids)
                rows = self.conn.execute(
                    f"select mailbox_id from sunny_accounts where id in ({marks})",
                    account_ids,
                ).fetchall()
                mailbox_ids = [int(row["mailbox_id"] or 0) for row in rows if int(row["mailbox_id"] or 0) > 0]

        completed_statuses: dict[int, str] = {}
        progress_rank = {"已注册": 1, "已接码": 2, "已反代": 3}

        def remember_completed(mailbox_id: int, status: str) -> None:
            current = completed_statuses.get(mailbox_id, "")
            if progress_rank.get(status, 0) >= progress_rank.get(current, 0):
                completed_statuses[mailbox_id] = status

        if mailbox_ids:
            marks = ",".join("?" for _ in mailbox_ids)
            rows = self.conn.execute(
                f"select mailbox_id,status,sub2api_status,metadata_json from sunny_accounts where mailbox_id in ({marks})",
                mailbox_ids,
            ).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    metadata = {}
                if not isinstance(metadata, dict) or str(metadata.get("task_id") or "") != self.task_id:
                    continue
                completed_status = str(metadata.get("completed_status") or "").strip()
                if not completed_status:
                    account_status = str(row["status"] or "").lower()
                    completed_status = {
                        "registered": "已注册",
                        "phone_bound": "已接码",
                        "reverse_proxied": "已反代",
                    }.get(account_status, "")
                if completed_status in {"已注册", "已接码", "已反代"}:
                    remember_completed(int(row["mailbox_id"] or 0), completed_status)

                if str(row["sub2api_status"] or "").lower() in {"imported", "success", "succeeded"}:
                    remember_completed(int(row["mailbox_id"] or 0), "已反代")

        mailbox_marks = ",".join("?" for _ in mailbox_ids)
        if mailbox_marks:
            mailbox_rows = self.conn.execute(
                f"select id,status from sunny_mailboxes where id in ({mailbox_marks})",
                mailbox_ids,
            ).fetchall()
            for row in mailbox_rows:
                current_status = str(row["status"] or "").strip()
                if current_status in {"已注册", "已接码", "已反代"}:
                    remember_completed(int(row["id"]), current_status)

        for mailbox_id, completed_status in completed_statuses.items():
            self.conn.execute(
                "update sunny_mailboxes set status=?,last_error='',updated_at=? where id=?",
                (completed_status, now_sql(), mailbox_id),
            )
        failed_ids = [mailbox_id for mailbox_id in mailbox_ids if mailbox_id not in completed_statuses]
        if failed_ids:
            marks = ",".join("?" for _ in failed_ids)
            self.conn.execute(
                f"update sunny_mailboxes set status='失败',last_error=?,updated_at=? where id in ({marks})",
                [reason, now_sql(), *failed_ids],
            )
        if completed_statuses or failed_ids:
            self.conn.commit()
        return {
            "completed": len(completed_statuses),
            "failed": len(failed_ids),
            "completed_mailbox_ids": sorted(completed_statuses),
            "completed_mailbox_statuses": {str(key): value for key, value in completed_statuses.items()},
            "failed_mailbox_ids": failed_ids,
        }

    def fetch_mailboxes(self, ids: list[int] | None = None, limit: int = 0) -> list[dict[str, Any]]:
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = self.conn.execute(f"select * from sunny_mailboxes where id in ({marks}) order by id asc", ids).fetchall()
        else:
            sql = "select * from sunny_mailboxes where enabled=1 and coalesce(status,'') not in ('disabled') order by id asc"
            if limit:
                sql += f" limit {int(limit)}"
            rows = self.conn.execute(sql).fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            self._hydrate_mailbox_auth(item)
        return items

    def _hydrate_mailbox_auth(self, mailbox: dict[str, Any]) -> None:
        """Fill mailbox OpenAI RT from account/session tables when the mailbox row is stale."""
        if mailbox.get("openai_rt"):
            return
        email = str(mailbox.get("email") or "")
        if not email:
            return
        row = self.conn.execute("select openai_rt from sunny_accounts where email=? and coalesce(openai_rt,'')<>''", (email,)).fetchone()
        if row and row["openai_rt"]:
            mailbox["openai_rt"] = row["openai_rt"]
            return
        row = self.conn.execute("select refresh_token from sunny_sessions where email=? and coalesce(refresh_token,'')<>''", (email,)).fetchone()
        if row and row["refresh_token"]:
            mailbox["openai_rt"] = row["refresh_token"]

    def fetch_accounts(self, ids: list[int] | None = None) -> list[dict[str, Any]]:
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = self.conn.execute(f"select * from sunny_accounts where id in ({marks}) order by id asc", ids).fetchall()
        else:
            rows = self.conn.execute("select * from sunny_accounts order by id asc").fetchall()
        return [dict(r) for r in rows]

    def fetch_session_by_email(self, email: str) -> dict[str, Any] | None:
        row = self.conn.execute("select * from sunny_sessions where email=?", (email,)).fetchone()
        return dict(row) if row else None

    def reserve_phone(self) -> dict[str, Any] | None:
        phone_cfg = self.get_config("phone")
        if phone_cfg and phone_cfg.get("pool_enabled") is False:
            return None
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                """
                select * from sunny_phones
                where enabled=1 and coalesce(status,'available') not in ('disabled','full','in_use')
                  and coalesce(success_count,0) < coalesce(max_success,3)
                  and (cooldown_until is null or cooldown_until='' or datetime(cooldown_until) <= datetime('now','localtime'))
                order by success_count asc, id asc limit 1
                """
            ).fetchone()
            if not row:
                self.conn.rollback()
                return None
            phone = dict(row)
            self.conn.execute("update sunny_phones set status=?, updated_at=? where id=?", ("in_use", now_sql(), phone["id"]))
            self.conn.commit()
            return phone
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise

    def mark_phone_success(self, phone_id: int, code: str = "") -> None:
        until = (datetime.now(app_timezone()) + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "update sunny_phones set success_count=coalesce(success_count,0)+1, status=case when coalesce(success_count,0)+1>=coalesce(max_success,3) then 'full' else 'cooldown' end, cooldown_until=?, last_code=?, last_used_at=?, updated_at=? where id=?",
            (until, code, now_sql(), now_sql(), phone_id),
        )
        self.conn.commit()

    def mark_phone_error(self, phone_id: int, error: str) -> None:
        self.conn.execute("update sunny_phones set status='available', last_error=?, updated_at=? where id=?", (error, now_sql(), phone_id))
        self.conn.commit()

    def usable_phone_count(self) -> int:
        phone_cfg = self.get_config("phone")
        if phone_cfg and phone_cfg.get("pool_enabled") is False:
            return 0
        row = self.conn.execute(
            """
            select count(*) as n from sunny_phones
            where enabled=1 and coalesce(status,'available') not in ('disabled','full','in_use')
              and coalesce(success_count,0) < coalesce(max_success,3)
              and (cooldown_until is null or cooldown_until='' or datetime(cooldown_until) <= datetime('now','localtime'))
            """
        ).fetchone()
        return int(row["n"] if row else 0)

    def smsbower_available(self) -> bool:
        phone_cfg = self.get_config("phone")
        return bool(phone_cfg.get("smsbower_enabled") and str(phone_cfg.get("smsbower_api_key") or "").strip())

    def smspool_available(self) -> bool:
        phone_cfg = self.get_config("phone")
        return bool(phone_cfg.get("smspool_enabled") and str(phone_cfg.get("smspool_api_key") or "").strip())

    def resolve_sms_provider_option(self, provider: str, kind: str, value: str, parent: str = "") -> dict[str, Any] | None:
        value = str(value or "").strip()
        if not value:
            return None
        params: list[Any] = [provider, kind]
        parent_clause = ""
        if parent:
            parent_clause = " and parent_value=?"
            params.append(parent)
        rows = self.conn.execute(
            f"select * from sunny_sms_provider_options where provider=? and kind=?{parent_clause}",
            params,
        ).fetchall()
        normalized = value.casefold()
        contains: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if str(item.get("value") or "").strip().casefold() == normalized:
                return item
            label = str(item.get("label") or "").strip()
            if label.casefold() == normalized:
                return item
            if normalized in label.casefold():
                contains.append(item)
        return min(contains, key=lambda item: len(str(item.get("label") or ""))) if contains else None

    def sms_provider_option_extra(self, option: dict[str, Any] | None) -> dict[str, Any]:
        if not option:
            return {}
        try:
            value = json.loads(str(option.get("extra_json") or "{}"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def reserve_sms_provider_number(self, provider: str, country: str = "", service: str = "") -> dict[str, Any] | None:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                """
                select * from sunny_sms_provider_numbers
                where provider=?
                  and (?='' or country=?)
                  and (?='' or service=?)
                  and coalesce(status,'available') not in ('disabled','in_use','full')
                  and coalesce(success_count,0) < coalesce(max_success,3)
                  and (cooldown_until is null or cooldown_until='' or datetime(cooldown_until) <= datetime('now','localtime'))
                order by success_count asc, last_used_at asc, id asc
                limit 1
                """,
                (provider, country, country, service, service),
            ).fetchone()
            if not row:
                self.conn.rollback()
                return None
            item = dict(row)
            self.conn.execute(
                "update sunny_sms_provider_numbers set status='in_use', updated_at=? where id=?",
                (now_sql(), item["id"]),
            )
            self.conn.commit()
            return item
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise

    def record_sms_provider_number(self, provider: str, phone_number: str, country: str = "", service: str = "", pool: str = "", order_id: str = "", token: str = "") -> None:
        if not phone_number:
            return
        row = self.conn.execute(
            "select id from sunny_sms_provider_numbers where provider=? and phone_number=? and country=? and service=?",
            (provider, phone_number, country, service),
        ).fetchone()
        values = {
            "provider": provider,
            "phone_number": phone_number,
            "country": country,
            "service": service,
            "pool": pool,
            "last_order_id": order_id,
            "token": token,
            "status": "in_use",
            "last_error": "",
            "last_used_at": now_sql(),
            "updated_at": now_sql(),
        }
        if row:
            sets = ",".join(f"{k}=?" for k in values)
            self.conn.execute(f"update sunny_sms_provider_numbers set {sets} where id=?", [*values.values(), row["id"]])
        else:
            values["created_at"] = now_sql()
            cols = ",".join(values)
            self.conn.execute(f"insert into sunny_sms_provider_numbers({cols}) values({','.join('?' for _ in values)})", list(values.values()))
        self.conn.commit()

    def mark_sms_provider_number_success(self, provider: str, phone_number: str, code: str = "") -> None:
        if not phone_number:
            return
        until = (datetime.now(app_timezone()) + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """
            update sunny_sms_provider_numbers
            set success_count=coalesce(success_count,0)+1,
                status=case when coalesce(success_count,0)+1>=coalesce(max_success,3) then 'full' else 'cooldown' end,
                cooldown_until=?,
                last_error='',
                last_used_at=?,
                updated_at=?
            where provider=? and phone_number=?
            """,
            (until, now_sql(), now_sql(), provider, phone_number),
        )
        self.conn.commit()

    def mark_sms_provider_number_error(self, provider: str, phone_number: str, error: str) -> None:
        if not phone_number:
            return
        self.conn.execute(
            "update sunny_sms_provider_numbers set status='available', last_error=?, updated_at=? where provider=? and phone_number=?",
            (error, now_sql(), provider, phone_number),
        )
        self.conn.commit()

    def get_config(self, key: str) -> dict[str, Any]:
        row = self.conn.execute("select value_json from sunny_configs where key=?", (key,)).fetchone()
        if not row:
            return {}
        try:
            data = json.loads(row["value_json"] or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def set_account_sub2api_status(self, email: str, status: str, sub2api_id: str = "", error: str = "") -> None:
        self.conn.execute("update sunny_accounts set sub2api_status=?, sub2api_id=?, last_error=?, updated_at=? where email=?", (status, sub2api_id, error, now_sql(), email))
        self.conn.commit()

    def upsert_account(self, email: str, **fields: Any) -> int:
        row = self.conn.execute("select id,status from sunny_accounts where email=?", (email,)).fetchone()
        base = {"email": email, "updated_at": now_sql(), **fields}
        if row:
            if "status" in base and str(base["status"] or "") != str(row["status"] or ""):
                base["status_changed_at"] = base["updated_at"]
            sets = ",".join(f"{k}=?" for k in base)
            self.conn.execute(f"update sunny_accounts set {sets} where id=?", [*base.values(), row["id"]])
            account_id = int(row["id"])
        else:
            base.setdefault("created_at", now_sql())
            cols = ",".join(base)
            marks = ",".join("?" for _ in base)
            cur = self.conn.execute(f"insert into sunny_accounts({cols}) values({marks})", list(base.values()))
            account_id = int(cur.lastrowid)
        self.conn.commit()
        return account_id

    def upsert_session(self, email: str, account_id: int, session: dict[str, Any], raw_line: str = "") -> None:
        values = {
            "account_id": account_id,
            "email": email,
            "access_token": session.get("access_token", ""),
            "refresh_token": session.get("refresh_token", "") or session.get("openai_rt", ""),
            "id_token": session.get("id_token", ""),
            "session_json": json.dumps(session.get("session_json", session), ensure_ascii=False) if not isinstance(session.get("session_json"), str) else session.get("session_json"),
            "storage_state_json": json.dumps(session.get("storage_state_json", {}), ensure_ascii=False) if not isinstance(session.get("storage_state_json"), str) else session.get("storage_state_json"),
            "raw_mailbox_line": raw_line,
            "last_refresh_at": now_sql(),
            "updated_at": now_sql(),
        }
        row = self.conn.execute("select id from sunny_sessions where email=?", (email,)).fetchone()
        if row:
            sets = ",".join(f"{k}=?" for k in values)
            self.conn.execute(f"update sunny_sessions set {sets} where id=?", [*values.values(), row["id"]])
        else:
            values["created_at"] = now_sql()
            cols = ",".join(values)
            self.conn.execute(f"insert into sunny_sessions({cols}) values({','.join('?' for _ in values)})", list(values.values()))
        self.conn.commit()

    def mark_mailbox(self, mailbox_id: int, status: str, error: str = "", openai_rt: str = "") -> None:
        if mailbox_id <= 0:
            return
        success_statuses = {"已注册", "已接码", "已反代"}
        sets = ["status=?", "last_error=?", "updated_at=?"]
        values: list[Any] = [status, error, now_sql()]
        if openai_rt:
            sets.append("openai_rt=?")
            values.append(openai_rt)
        if status in success_statuses:
            sets.append("registered_at=coalesce(registered_at, ?)")
            values.append(now_sql())
        values.append(mailbox_id)
        self.conn.execute(f"update sunny_mailboxes set {','.join(sets)} where id=?", values)
        self.conn.commit()

    def mailbox_status(self, mailbox_id: int) -> str:
        if mailbox_id <= 0:
            return ""
        row = self.conn.execute("select status from sunny_mailboxes where id=?", (mailbox_id,)).fetchone()
        return str(row["status"] if row else "")

    def mark_mailbox_by_email(self, email: str, status: str, error: str = "", openai_rt: str = "") -> None:
        if not email:
            return
        success_statuses = {"已注册", "已接码", "已反代"}
        sets = ["status=?", "last_error=?", "updated_at=?"]
        values: list[Any] = [status, error, now_sql()]
        if openai_rt:
            sets.append("openai_rt=?")
            values.append(openai_rt)
        if status in success_statuses:
            sets.append("registered_at=coalesce(registered_at, ?)")
            values.append(now_sql())
        values.append(email)
        self.conn.execute(f"update sunny_mailboxes set {','.join(sets)} where email=?", values)
        self.conn.commit()
