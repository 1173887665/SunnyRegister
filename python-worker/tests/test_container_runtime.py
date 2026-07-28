from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from sunny_core.browser_backend import camoufox_runtime_error
from sunny_core.mailbox import HotmailReader, MailAccount, MailboxAccessError, _request_outlook_access_token, parse_account_line


def account(email: str = "user@example.com") -> MailAccount:
    return MailAccount(
        email=email,
        password="password",
        client_id="client-id",
        refresh_token="refresh-token",
        raw=f"{email}----password----client-id----refresh-token",
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


class OutlookGraphCredentialTests(unittest.TestCase):
    def test_expired_refresh_token_is_classified_and_stops_routing(self) -> None:
        response = Mock()
        response.ok = False
        response.status_code = 400
        response.text = '{"error":"invalid_grant"}'
        response.json.return_value = {
            "error": "invalid_grant",
            "error_description": "The user could not be authenticated as the grant is expired. The user must sign in again.",
        }
        with patch("sunny_core.mailbox.requests.post", return_value=response):
            with self.assertRaises(MailboxAccessError) as raised:
                _request_outlook_access_token(account(), {"name": "TEST", "url": "https://example.test/token", "scope": ""}, None)

        self.assertEqual(raised.exception.code, "mailbox_credential_expired")
        self.assertTrue(raised.exception.terminal)

    def test_graph_credential_is_detected_and_reads_messages(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "value": [{
                "id": "message-id",
                "subject": "Your ChatGPT code is 123456",
                "from": {"emailAddress": {"name": "OpenAI", "address": "noreply@openai.com"}},
                "toRecipients": [{"emailAddress": {"address": "user@example.com"}}],
                "receivedDateTime": "2026-07-22T08:00:00Z",
                "bodyPreview": "Use 123456 to continue",
                "body": {"contentType": "html", "content": "<p>Use <b>123456</b> to continue</p>"},
            }],
        }
        reader = HotmailReader(account(), None)
        with (
            patch("sunny_core.mailbox._request_outlook_access_token", return_value="graph-access-token"),
            patch("sunny_core.mailbox.requests.get", return_value=response) as graph_get,
        ):
            reader.connect()
            message = reader.latest_message()

        self.assertEqual(reader.graph_access_token, "graph-access-token")
        self.assertEqual(message["source"], "graph")
        self.assertEqual(message["otp"], "123456")
        self.assertIn("noreply@openai.com", message["from"])
        self.assertEqual(graph_get.call_args.kwargs["headers"]["Authorization"], "Bearer graph-access-token")

    def test_graph_scope_failure_falls_back_to_imap(self) -> None:
        reader = HotmailReader(account(), None)
        with (
            patch.object(reader, "_connect_graph_routes", return_value=False),
            patch("sunny_core.mailbox._request_outlook_access_token", return_value="imap-access-token"),
            patch.object(reader, "_connect_with_access_token_routes") as connect_imap,
        ):
            reader.connect()

        connect_imap.assert_called_once_with("imap-access-token", "LIVE token-direct")


class HotmailCredentialCompatibilityTests(unittest.TestCase):
    def test_hotmail_four_field_credential_is_accepted(self) -> None:
        parsed = parse_account_line("reader@hotmail.com----password----client-id----refresh-token")

        self.assertEqual(parsed.email, "reader@hotmail.com")
        self.assertEqual(parsed.client_id, "client-id")
        self.assertEqual(parsed.refresh_token, "refresh-token")

    def test_hotmail_dual_token_prefers_graph(self) -> None:
        response = Mock()
        response.ok = True
        response.json.return_value = {"value": []}
        reader = HotmailReader(account("reader@hotmail.com"), None)
        with (
            patch("sunny_core.mailbox._request_outlook_access_token", return_value="dual-access-token"),
            patch("sunny_core.mailbox.requests.get", return_value=response),
            patch.object(reader, "_connect_with_access_token_routes") as connect_imap,
        ):
            reader.connect()

        self.assertEqual(reader.graph_access_token, "dual-access-token")
        connect_imap.assert_not_called()

    def test_hotmail_imap_pop3_credential_falls_back_to_imap(self) -> None:
        reader = HotmailReader(account("reader@hotmail.com"), None)
        with (
            patch.object(reader, "_connect_graph_routes", return_value=False),
            patch("sunny_core.mailbox._request_outlook_access_token", return_value="outlook-scope-token"),
            patch.object(reader, "_connect_with_access_token_routes") as connect_imap,
        ):
            reader.connect()

        connect_imap.assert_called_once_with("outlook-scope-token", "LIVE token-direct")


if __name__ == "__main__":
    unittest.main()
