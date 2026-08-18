from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
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

    def test_cloudflare_html_on_all_routes_is_blocked_not_invalid(self) -> None:
        blocked = {"status": "blocked", "error": "Cloudflare HTML"}
        with patch("sunny_core.access_token_probe._request", side_effect=[blocked, blocked]) as request:
            result = probe_access_token("current-token", "http://proxy.example:8080")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("未判定令牌失效", result["error"])
        self.assertEqual(request.call_count, 2)

    def test_proxy_traffic_is_returned_to_backend(self) -> None:
        class FakeMeter:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def snapshot(self) -> dict:
                return {"requests": 1, "total_bytes": 456}

        with (
            patch("sunny_core.access_token_probe.ProxyTrafficMeter", side_effect=FakeMeter) as meter,
            patch("sunny_core.access_token_probe.use_traffic_meter", side_effect=lambda value: nullcontext(value)),
            patch("sunny_core.access_token_probe._request", return_value={"status": "valid"}),
        ):
            result = probe_access_token("current-token", "http://proxy.example:8080")
        self.assertEqual(result["traffic"], {"requests": 1, "total_bytes": 456})
        self.assertTrue(meter.call_args.kwargs["tracked_proxy"])


if __name__ == "__main__":
    unittest.main()
