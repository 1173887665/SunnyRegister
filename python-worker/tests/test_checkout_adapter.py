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
    def test_start_checkout_maps_sunny_pool_names_to_reference_routes(self) -> None:
        with patch.object(sunny_adapter.STORE, "create", create=True, return_value="job-3") as create:
            result = sunny_adapter.start_checkout({
                "token": "token",
                "link_type": " PayPal ",
                "checkout_kind": "oaics",
                "checkout_proxies": ["checkout-proxy"],
                "promotion_proxies": ["promotion-proxy"],
            })
        self.assertEqual(result, "job-3")
        options = create.call_args.args[0]
        self.assertEqual(options["link_type"], "paypal")
        self.assertEqual(options["paypal_checkout_mode"], "oaics")
        self.assertTrue(options["oaics_paypal"])
        self.assertTrue(options["named_proxy_pools"])
        self.assertEqual(options["entry_proxies"], ["promotion-proxy"])
        self.assertEqual(options["exit_proxies"], ["checkout-proxy"])

    def test_start_checkout_leaves_non_paypal_routes_on_default_workflow(self) -> None:
        with patch.object(sunny_adapter.STORE, "create", create=True, return_value="job-4") as create:
            sunny_adapter.start_checkout({"token": "token", "link_type": "hosted"})

        options = create.call_args.args[0]
        self.assertFalse(options["oaics_paypal"])

    def test_start_checkout_routes_cs_live_paypal_to_reference_workflow(self) -> None:
        with patch.object(sunny_adapter.STORE, "create", create=True, return_value="job-cs") as create:
            sunny_adapter.start_checkout({
                "token": "token",
                "link_type": "paypal",
                "checkout_kind": "cs_live",
            })

        options = create.call_args.args[0]
        self.assertEqual(options["paypal_checkout_mode"], "cs_live")
        self.assertFalse(options["oaics_paypal"])

    def test_start_checkout_auto_detects_unknown_paypal_checkout_type(self) -> None:
        with patch.object(sunny_adapter.STORE, "create", create=True, return_value="job-auto") as create:
            sunny_adapter.start_checkout({"token": "token", "link_type": "paypal"})

        options = create.call_args.args[0]
        self.assertEqual(options["paypal_checkout_mode"], "auto")
        self.assertTrue(options["oaics_paypal"])

    def test_start_checkout_keeps_pix_checkout_and_promotion_pools_separate(self) -> None:
        with patch.object(sunny_adapter.STORE, "create", create=True, return_value="job-5") as create:
            sunny_adapter.start_checkout({
                "token": "token",
                "link_type": "pix",
                "checkout_proxies": ["checkout-proxy"],
                "promotion_proxies": ["promotion-proxy"],
            })

        options = create.call_args.args[0]
        self.assertEqual(options["entry_proxies"], ["promotion-proxy"])
        self.assertEqual(options["exit_proxies"], ["checkout-proxy"])

    def test_start_checkout_maps_gcash_route_countries_by_proxy_role(self) -> None:
        with patch.object(sunny_adapter.STORE, "create", create=True, return_value="job-6") as create:
            sunny_adapter.start_checkout({
                "token": "token",
                "link_type": "gcash",
                "country": "PH",
                "promo_country": "VN",
            })

        options = create.call_args.args[0]
        self.assertEqual(options["entry_proxy_country"], "VN")
        self.assertEqual(options["exit_proxy_country"], "PH")

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

    def test_checkout_status_translates_legacy_proxy_pool_labels(self) -> None:
        job = {
            "status": "running",
            "percent": 25,
            "logs": [
                {"message": "代理池 1 用于优惠检查，代理池2用于创建 Checkout"},
            ],
            "result": {},
        }

        with patch.object(sunny_adapter.STORE, "get", return_value=job):
            result = sunny_adapter.checkout_status("job-legacy")

        message = result["logs"][0]["message"]
        self.assertEqual(message, "Promotion代理池 用于优惠检查，Checkout代理池用于创建 Checkout")
        self.assertNotIn("代理池 1", message)
        self.assertNotIn("代理池2", message)

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
                "checkout_kind": "cs_live",
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
        self.assertEqual(payload["checkout_kind"], "cs_live")
        self.assertEqual(payload["paypal_link"], "https://pay.example/approve")
        self.assertEqual(payload["payment_methods"], ["card", "paypal"])
        self.assertTrue(payload["promo_applied"])


if __name__ == "__main__":
    unittest.main()
