from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sunny_core.browser_backend import camoufox_runtime_error
from sunny_core.mailbox import HotmailReader, MailAccount


def account() -> MailAccount:
    return MailAccount(
        email="user@example.com",
        password="password",
        client_id="client-id",
        refresh_token="refresh-token",
        raw="user@example.com----password----client-id----refresh-token",
    )


class CamoufoxRuntimeTests(unittest.TestCase):
    def test_container_reports_missing_linux_library(self) -> None:
        def load_library(name: str):
            if name == "libgtk-3.so.0":
                raise OSError("not found")
            return object()

        with (
            patch("sunny_core.browser_backend.sys.platform", "linux"),
            patch.dict(os.environ, {"SUNNY_CONTAINERIZED": "true"}),
            patch("sunny_core.browser_backend.ctypes.CDLL", side_effect=load_library),
        ):
            error = camoufox_runtime_error()

        self.assertIn("libgtk-3.so.0", error)


class OutlookImapRouteTests(unittest.TestCase):
    def test_direct_then_dedicated_proxy(self) -> None:
        reader = HotmailReader(account(), None, "http://task-proxy.example:8080")
        with patch.dict(
            os.environ,
            {
                "OUTLOOK_IMAP_DIRECT_FIRST": "true",
                "OUTLOOK_IMAP_PROXY": "socks5://imap-proxy.example:1080",
            },
        ):
            self.assertEqual(
                reader._imap_proxy_candidates(),
                ["", "socks5://imap-proxy.example:1080"],
            )

    def test_task_proxy_is_used_when_dedicated_proxy_is_empty(self) -> None:
        reader = HotmailReader(account(), None, "http://task-proxy.example:8080")
        with patch.dict(
            os.environ,
            {"OUTLOOK_IMAP_DIRECT_FIRST": "false", "OUTLOOK_IMAP_PROXY": ""},
        ):
            self.assertEqual(
                reader._imap_proxy_candidates(),
                ["http://task-proxy.example:8080", ""],
            )


if __name__ == "__main__":
    unittest.main()
