from __future__ import annotations

import unittest
import sys
from types import ModuleType
from unittest.mock import patch

class FakeStore:
    def get(self, _job_id: str, public: bool = False):
        return None


fake_app = ModuleType("app")
fake_app.STORE = FakeStore()
previous_app = sys.modules.get("app")
sys.modules["app"] = fake_app

from tools.pay153_checkout import sunny_adapter

if previous_app is None:
    sys.modules.pop("app", None)
else:
    sys.modules["app"] = previous_app


class CheckoutAdapterTests(unittest.TestCase):
    def test_checkout_status_returns_ordered_sanitized_logs(self) -> None:
        token = "eyJ" + "a" * 80
        job = {
            "status": "running",
            "percent": 50,
            "text": "正在提链",
            "error": "",
            "logs": [
                {"sequence": 41, "time": "10:00:00", "message": f"AT={token}", "major": False},
                {"sequence": 42, "time": "10:00:01", "message": "proxy=socks5://user:pass@127.0.0.1:1080", "major": True},
            ],
            "result": {},
        }

        with patch.object(sunny_adapter.STORE, "get", return_value=job) as get_job:
            result = sunny_adapter.checkout_status("job-1")

        get_job.assert_called_once_with("job-1", public=False)
        self.assertEqual([item["sequence"] for item in result["logs"]], [41, 42])
        self.assertNotIn(token, result["logs"][0]["message"])
        self.assertIn("[TOKEN]", result["logs"][0]["message"])
        self.assertNotIn("user:pass", result["logs"][1]["message"])
        self.assertIn("socks5://[PROXY]@", result["logs"][1]["message"])

    def test_checkout_status_preserves_reference_result_fields(self) -> None:
        job = {
            "status": "done",
            "percent": 100,
            "text": "提取完成",
            "error": "",
            "logs": [],
            "result": {
                "plan": "plus",
                "account_email": "user@example.com",
                "link_type": "paypal",
                "checkout_session_id": "cs_live_123",
                "paypal_url": "https://pay.example/approve",
                "payment_methods": ["card", "paypal"],
                "checkout_amount": 0,
                "promo_requested": True,
                "promo_applied": True,
                "country": "US",
                "currency": "USD",
            },
        }
        with patch.object(sunny_adapter.STORE, "get", return_value=job):
            result = sunny_adapter.checkout_status("job-2")
        payload = result["result"]
        self.assertEqual(payload["account_email"], "user@example.com")
        self.assertEqual(payload["checkout_session_id"], "cs_live_123")
        self.assertEqual(payload["paypal_link"], "https://pay.example/approve")
        self.assertEqual(payload["payment_methods"], ["card", "paypal"])
        self.assertTrue(payload["promo_applied"])


if __name__ == "__main__":
    unittest.main()
