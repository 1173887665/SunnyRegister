from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sunny_core.access_token_probe import _classify, probe_access_token


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class AccessTokenProbeTests(unittest.TestCase):
    def test_token_invalidated_response_is_invalid(self) -> None:
        result = _classify(FakeResponse(401, {
            "error": {
                "message": "Your authentication token has been invalidated. Please try signing in again.",
                "type": "invalid_request_error",
                "code": "token_invalidated",
            },
            "status": 401,
        }))
        self.assertEqual(result["status"], "invalid")
        self.assertIn("token_invalidated", result["error"])

    def test_models_response_is_valid(self) -> None:
        result = _classify(FakeResponse(200, {"title": "ChatGPT", "models": [], "versions": []}))
        self.assertEqual(result["status"], "valid")

    def test_cloudflare_html_on_proxy_falls_back_to_direct(self) -> None:
        blocked = {"status": "blocked", "error": "Cloudflare HTML"}
        invalid = {"status": "invalid", "error": "token_invalidated"}
        with patch("sunny_core.access_token_probe._request", side_effect=[blocked, invalid]) as request:
            result = probe_access_token("expired-token", "http://proxy.example:8080")
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["source"], "服务器直连")
        self.assertEqual(request.call_args_list[0].args[1], "http://proxy.example:8080")
        self.assertEqual(request.call_args_list[1].args[1], "")


if __name__ == "__main__":
    unittest.main()
