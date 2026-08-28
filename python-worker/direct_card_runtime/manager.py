"""SunnyRegister adapter for the vendored direct-card protocol service."""
from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from . import protocol_server
from standalone_core.fingerprint_store import optimize_fingerprint_store  # noqa: E402

_INITIALIZE_LOCK = threading.Lock()
_initialized = False


def _initialize() -> None:
    global _initialized
    if _initialized:
        return
    with _INITIALIZE_LOCK:
        if not _initialized:
            optimize_fingerprint_store("US")
            _initialized = True


def info() -> dict[str, Any]:
    _initialize()
    with protocol_server.TASKS_LOCK:
        task_count = len(protocol_server.TASKS)
    return {
        "ok": True,
        "service": "direct-card-protocol",
        "fingerprint_provider": "registration-profile",
        "fingerprint_sticky_per_account": True,
        "fingerprint_batch_limit": 500,
        "aligned_batch_limit": protocol_server.ALIGNED_BATCH_LIMIT,
        "task_count": task_count,
    }


def preflight(payload: dict[str, Any]) -> dict[str, Any]:
    _initialize()
    result = protocol_server.preflight(payload)
    if not str(result.get("publishable_key") or "").startswith("pk_"):
        raise RuntimeError("preflight did not return a publishable key")
    return result


def allocate_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    _initialize()
    return protocol_server.allocate_fingerprint(payload)


def allocate_fingerprints(payload: dict[str, Any]) -> dict[str, Any]:
    _initialize()
    return protocol_server.allocate_fingerprints(payload)


def address(payload: dict[str, Any]) -> dict[str, Any]:
    return protocol_server.fetch_billing_address(payload)


def start(payload: dict[str, Any]) -> dict[str, Any]:
    protocol_server.validate_payload(payload, require_payment_method=True)
    task_id = uuid.uuid4().hex
    protocol_server._set_task(task_id, status="queued", progress=0, stage="独立 HTTP 任务排队")
    threading.Thread(
        target=protocol_server._run_task,
        args=(task_id, payload),
        name=f"direct-card-{task_id[:8]}",
        daemon=True,
    ).start()
    return {"ok": True, "task_id": task_id}


def start_batch(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("tasks")
    if not isinstance(source, list) or not source:
        raise ValueError("tasks must be a non-empty list")
    if len(source) > protocol_server.ALIGNED_BATCH_LIMIT:
        raise ValueError(
            f"aligned batch cannot exceed {protocol_server.ALIGNED_BATCH_LIMIT} accounts"
        )
    prepared: list[tuple[str, dict[str, Any], str]] = []
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("task item must be an object")
        client_id = str(item.get("client_id") or "")[:100]
        task_payload = item.get("payload")
        if not isinstance(task_payload, dict):
            raise ValueError("task payload must be an object")
        protocol_server.validate_payload(task_payload, require_payment_method=True)
        prepared.append((client_id, task_payload, uuid.uuid4().hex))

    start_group = uuid.uuid4().hex
    start_delay_ms = max(100, min(1000, int(payload.get("start_delay_ms") or 250)))
    start_at_monotonic = time.perf_counter() + (start_delay_ms / 1000)
    start_at_epoch = time.time() + (start_delay_ms / 1000)
    for client_id, task_payload, task_id in prepared:
        protocol_server._set_task(
            task_id,
            status="queued",
            progress=0,
            stage="等待同批账号同步起跑",
            start_group=start_group,
        )
        threading.Thread(
            target=protocol_server._run_task,
            args=(task_id, task_payload, start_at_monotonic, start_group),
            name=f"direct-card-{task_id[:8]}",
            daemon=True,
        ).start()
    return {
        "ok": True,
        "start_group": start_group,
        "start_at": start_at_epoch,
        "items": [
            {"client_id": client_id, "task_id": task_id}
            for client_id, _task_payload, task_id in prepared
        ],
    }


def get(task_id: str) -> dict[str, Any] | None:
    with protocol_server.TASKS_LOCK:
        task = dict(protocol_server.TASKS.get(str(task_id or "")) or {})
    return task or None


def list_tasks() -> list[dict[str, Any]]:
    with protocol_server.TASKS_LOCK:
        tasks = [dict(item) for item in protocol_server.TASKS.values()]
    return sorted(
        tasks,
        key=lambda item: float(item.get("started_at") or item.get("finished_at") or 0),
        reverse=True,
    )


def clear() -> dict[str, Any]:
    with protocol_server.TASKS_LOCK:
        removable = [
            task_id
            for task_id, task in protocol_server.TASKS.items()
            if str(task.get("status") or "") not in {"queued", "running"}
        ]
        for task_id in removable:
            protocol_server.TASKS.pop(task_id, None)
    return {"ok": True, "removed": len(removable)}
