import json
import threading
import time
from unittest.mock import Mock, patch

from sunny_core import worker


def test_remail_provisioner_orders_and_persists_one_pickup_mailbox():
    db = Mock()
    db.task_id = "task-1"
    db.get_config.return_value = {
        "enabled": True,
        "base_url": "https://remail.example",
        "api_key": "secret",
        "project_id": 7,
        "service_mode": "purchase",
        "supply": "private_first",
    }
    db.create_remail_mailbox.return_value = {"id": 12, "email": "user@example.com"}
    response = Mock(ok=True)
    response.json.return_value = {"orderNo": "R-1", "deliveryEmail": "user@example.com", "serviceToken": "st-1"}

    with patch.object(worker.requests, "request", return_value=response) as request:
        mailbox = worker.RemailMailboxProvisioner(db).purchase(1)

    assert mailbox["id"] == 12
    assert request.call_count == 1
    assert request.call_args.args[:2] == ("POST", "https://remail.example/v1/open/orders")
    assert request.call_args.kwargs["params"] == {"serviceMode": "purchase", "supply": "private_first"}
    db.create_remail_mailbox.assert_called_once_with(
        "user@example.com",
        "https://remail.example/v1/pickup?email=user@example.com&token=st-1",
    )


class FakeTaskDB:
    instance = None

    def __init__(self, task_id):
        self.task_id = task_id
        self.payload = {"identity": "remail", "count": 10, "concurrency": 3, "proxy_enabled": False}
        self.updates = []
        self.events = []
        FakeTaskDB.instance = self

    def task(self):
        return {"type": "sunny_register", "payload_json": json.dumps(self.payload), "status": "pending"}

    def cancel_requested(self):
        return False

    def ensure_not_cancelled(self):
        return None

    def update_task(self, **fields):
        self.updates.append(fields)

    def event(self, message, level="info", typ="log", detail=None):
        self.events.append((message, level, detail))

    def close(self):
        return None


def test_remail_task_orders_exact_count_with_bounded_inflight_slots():
    lock = threading.Lock()
    purchased = []
    completed = []
    max_unfinished = 0

    class Provisioner:
        def __init__(self, _db):
            pass

        def purchase(self, sequence):
            nonlocal max_unfinished
            with lock:
                purchased.append(sequence)
                max_unfinished = max(max_unfinished, len(purchased) - len(completed))
            return {"id": sequence, "email": f"user{sequence}@example.com", "mailbox_type": "remail", "mailbox_channel": "remail_api"}

    def run_one(_task_id, _task_type, _payload, mailbox, idx, _total, _policy):
        time.sleep(0.01)
        with lock:
            completed.append(idx)
        return idx, True, {"email": mailbox["email"], "auth_action": "register"}

    with (
        patch.object(worker, "SunnyDB", FakeTaskDB),
        patch.object(worker, "RemailMailboxProvisioner", Provisioner),
        patch.object(worker, "_run_one_isolated", side_effect=run_one),
        patch.object(worker, "_log_proxy_startup"),
    ):
        worker.run_sunny_task("task-remail-lazy")

    assert purchased == list(range(1, 11))
    assert sorted(completed) == list(range(1, 11))
    assert max_unfinished <= 3
    final = FakeTaskDB.instance.updates[-1]
    assert final["status"] == "succeeded"
    assert json.loads(final["result_json"])["success"] == 10


def test_remail_balance_failure_stops_new_orders_after_running_slots_finish():
    completed = []

    class Provisioner:
        def __init__(self, _db):
            pass

        def purchase(self, sequence):
            if sequence == 4:
                raise worker.RemailOrderError("balance insufficient", insufficient_balance=True)
            return {"id": sequence, "email": f"user{sequence}@example.com", "mailbox_type": "remail", "mailbox_channel": "remail_api"}

    def run_one(_task_id, _task_type, _payload, mailbox, idx, _total, _policy):
        time.sleep(0.01)
        completed.append(idx)
        return idx, True, {"email": mailbox["email"], "auth_action": "register"}

    with (
        patch.object(worker, "SunnyDB", FakeTaskDB),
        patch.object(worker, "RemailMailboxProvisioner", Provisioner),
        patch.object(worker, "_run_one_isolated", side_effect=run_one),
        patch.object(worker, "_log_proxy_startup"),
    ):
        worker.run_sunny_task("task-remail-balance")

    assert sorted(completed) == [1, 2, 3]
    final = FakeTaskDB.instance.updates[-1]
    result = json.loads(final["result_json"])
    assert final["status"] == "failed"
    assert "余额不足" in result["provider_stop_reason"]
    assert result["success"] == 3
