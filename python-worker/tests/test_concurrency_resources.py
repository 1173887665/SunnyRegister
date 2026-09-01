from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sunny_core.db import SunnyDB
from sunny_core.worker import _prepare_register_proxy, _proxy_pool_candidates, _proxy_snapshot, _run_one_with_proxy_retry


class ProxySnapshotTests(unittest.TestCase):
    def test_registration_proxy_retry_rotates_only_route_failures(self) -> None:
        class FakeDB:
            def __init__(self) -> None:
                self.events: list[tuple] = []

            def ensure_not_cancelled(self) -> None:
                return None

            def event(self, *args, **kwargs) -> None:
                self.events.append((args, kwargs))

        payload = {
            "proxy_enabled": True,
            "proxy_pool": ["http://one.example:1000", "http://two.example:2000"],
            "proxy_ids": [11, 12],
        }
        mailbox = {"email": "user@example.com"}
        db = FakeDB()
        calls: list[dict] = []

        def run_one(_db, _task_type, current_payload, *_args):
            calls.append(current_payload)
            if current_payload.get("_excluded_register_proxies"):
                return True, {"proxy": "http://two.example:2000"}
            return False, "proxy CONNECT failed: HTTP/1.1 407 Proxy Authentication Required"

        with patch("sunny_core.worker._run_one", side_effect=run_one), patch(
            "sunny_core.worker._interruptible_delay"
        ):
            ok, result = _run_one_with_proxy_retry(db, "sunny_register", payload, mailbox, 1, 2)

        self.assertTrue(ok)
        self.assertEqual(result["proxy"], "http://two.example:2000")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["_excluded_register_proxies"], ["http://one.example:1000"])
        self.assertTrue(any("建立新上下文重试" in str(args[0]) for args, _kwargs in db.events))

    def test_registration_proxy_retry_does_not_repeat_auth_flow_failures(self) -> None:
        class FakeDB:
            def ensure_not_cancelled(self) -> None:
                return None

            def event(self, *args, **kwargs) -> None:
                pass

        payload = {
            "proxy_enabled": True,
            "proxy_pool": ["http://one.example:1000", "http://two.example:2000"],
            "proxy_ids": [11, 12],
        }
        mailbox = {"email": "user@example.com"}
        for error in (
            "EmailOtpValidate failed: wrong_email_otp_code",
            "browser challenge required",
        ):
            with self.subTest(error=error), patch(
                "sunny_core.worker._run_one", return_value=(False, error)
            ) as run_one, patch("sunny_core.worker._interruptible_delay"):
                ok, result = _run_one_with_proxy_retry(FakeDB(), "sunny_register", payload, mailbox, 1, 2)

            self.assertFalse(ok)
            self.assertEqual(result, error)
            run_one.assert_called_once()

    def test_round_robin_and_container_host_mapping(self) -> None:
        payload = {
            "proxy_enabled": True,
            "register_proxy": "http://ignored.example:8080",
            "proxy_pool": [
                "user:pass@127.0.0.1:1000",
                "socks5://user:pass@two.example:2000",
            ],
        }
        with patch.dict(os.environ, {"SUNNY_CONTAINERIZED": "true"}):
            first = _proxy_snapshot(payload, 0)["register"]
            second = _proxy_snapshot(payload, 1)["register"]
            wrapped = _proxy_snapshot(payload, 2)["register"]

        self.assertEqual(first, "http://user:pass@host.docker.internal:1000")
        self.assertEqual(second, "socks5://user:pass@two.example:2000")
        self.assertEqual(wrapped, first)

    def test_failed_proxy_rotates_without_mutating_pool_state(self) -> None:
        class FakeDB:
            def __init__(self) -> None:
                self.events: list[tuple] = []

            def proxy_is_usable(self, proxy_id: int) -> bool:
                return True

            def event(self, *args, **kwargs) -> None:
                self.events.append((args, kwargs))

        payload = {
            "proxy_enabled": True,
            "proxy_pool": [
                "http://one.example:1000",
                "http://two.example:2000",
            ],
            "proxy_ids": [11, 12],
        }
        db = FakeDB()
        checks = [
            {"ok": False, "error": "wrong version number", "latency_ms": 25},
            {"ok": True, "latency_ms": 40},
        ]

        with patch("sunny_core.worker.proxy_target_tls_check", side_effect=checks) as check:
            selected = _prepare_register_proxy(db, payload, "user@example.com", slot=1)

        self.assertEqual([call.args[0] for call in check.call_args_list], [
            "http://two.example:2000",
            "http://one.example:1000",
        ])
        self.assertEqual(selected["register"], "http://one.example:1000")
        self.assertEqual(selected["proxy_id"], 11)
        self.assertFalse(any("已置为失效" in str(args[0]) or "invalid" in str(args[0]).lower() for args, _kwargs in db.events))
        self.assertTrue(any("不修改代理池状态" in str(args[0]) for args, _kwargs in db.events))

    def test_proxy_snapshot_deduplicates_repeated_endpoints(self) -> None:
        payload = {
            "proxy_enabled": True,
            "proxy_pool": ["http://same.example:8080"] * 50 + ["http://other.example:8081"],
            "proxy_ids": list(range(51)),
        }
        candidates = _proxy_pool_candidates(payload)
        self.assertEqual([item["register"] for item in candidates], [
            "http://same.example:8080", "http://other.example:8081",
        ])

    def test_excluding_all_pool_endpoints_uses_local_fallback(self) -> None:
        class FakeDB:
            def event(self, *args, **kwargs) -> None:
                pass

            def proxy_is_usable(self, proxy_id: int) -> bool:
                return True

        payload = {
            "proxy_enabled": True,
            "proxy_pool": ["http://pool.example:8080"],
            "proxy_ids": [11],
            "local_proxy": "http://local.example:7890",
            "_excluded_register_proxies": ["http://pool.example:8080"],
        }
        with patch("sunny_core.worker.proxy_target_tls_check", return_value={"ok": True, "latency_ms": 1}):
            selected = _prepare_register_proxy(FakeDB(), payload, "user@example.com")
        self.assertEqual(selected["register"], "http://local.example:7890")
        self.assertEqual(selected["mode"], "local_proxy_fallback")


class PhoneReservationTests(unittest.TestCase):
    def test_concurrent_workers_reserve_distinct_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "sunny.db"
            conn = sqlite3.connect(database)
            conn.executescript(
                """
                create table sunny_configs (
                    key text primary key,
                    value_json text,
                    created_at datetime,
                    updated_at datetime
                );
                insert into sunny_configs(key, value_json) values ('phone', '{"pool_enabled": true}');
                create table sunny_phones (
                    id integer primary key autoincrement,
                    number text,
                    sms_url text,
                    status text default 'available',
                    enabled integer default 1,
                    success_count integer default 0,
                    max_success integer default 3,
                    cooldown_until datetime,
                    last_error text default '',
                    last_code text default '',
                    last_used_at datetime,
                    created_at datetime,
                    updated_at datetime
                );
                insert into sunny_phones(number, enabled) values ('+12025550101', 1);
                insert into sunny_phones(number, enabled) values ('+12025550102', 1);
                """
            )
            conn.commit()
            conn.close()

            def reserve() -> int | None:
                db = SunnyDB("test-task", ensure_schema=False)
                try:
                    item = db.reserve_phone()
                    return int(item["id"]) if item else None
                finally:
                    db.close()

            with patch.dict(os.environ, {"ACCOUNT_MANAGER_DATABASE_URL": str(database)}):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    reservations = list(pool.map(lambda _: reserve(), range(4)))

            allocated = [item for item in reservations if item is not None]
            self.assertEqual(len(allocated), 2)
            self.assertEqual(len(set(allocated)), 2)


if __name__ == "__main__":
    unittest.main()
