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


if __name__ == "__main__":
    unittest.main()
