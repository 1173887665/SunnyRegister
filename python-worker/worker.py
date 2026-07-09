from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("ACCOUNT_MANAGER_DATABASE_URL", "sqlite:////app/data/account_manager.db")

ORIGINAL_RUNTIME_ENABLED = os.getenv("ORIGINAL_RUNTIME_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
ORIGINAL_APP_PATH = Path(os.getenv("ORIGINAL_APP_PATH", "/app/original")).resolve()
if ORIGINAL_RUNTIME_ENABLED and str(ORIGINAL_APP_PATH) not in sys.path:
    sys.path.insert(0, str(ORIGINAL_APP_PATH))

WORKER_TOKEN = os.getenv("PYTHON_WORKER_TOKEN", "").strip()

app = FastAPI(title="SunnyRegister Python Automation Worker", version="1.0.0")
_state_lock = threading.Lock()
_running: set[str] = set()
_boot_error: Optional[str] = None
_booted = False


def _check_token(auth: str | None) -> None:
    if not WORKER_TOKEN:
        return
    expected = f"Bearer {WORKER_TOKEN}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized worker token")


def bootstrap_original_runtime() -> None:
    global _booted, _boot_error
    if not ORIGINAL_RUNTIME_ENABLED:
        raise RuntimeError("original runtime is disabled; SunnyRegister tasks do not require it")
    if _booted:
        return
    with _state_lock:
        if _booted:
            return
        try:
            if not ORIGINAL_APP_PATH.exists():
                raise RuntimeError(f"ORIGINAL_APP_PATH not found: {ORIGINAL_APP_PATH}")
            from core.db import init_db
            from core.registry import load_all
            from providers.registry import load_all as load_providers

            init_db()
            load_all()
            load_providers()
            _booted = True
            _boot_error = None
            print(f"[worker] original runtime booted from {ORIGINAL_APP_PATH}", flush=True)
        except Exception:
            _boot_error = traceback.format_exc()
            print("[worker] bootstrap failed:\n" + _boot_error, flush=True)
            raise


@app.on_event("startup")
def on_startup() -> None:
    if not ORIGINAL_RUNTIME_ENABLED:
        print("[worker] original runtime disabled; SunnyRegister worker ready", flush=True)
        return
    try:
        bootstrap_original_runtime()
    except Exception:
        # Keep HTTP server alive so Go can report a meaningful worker status.
        pass


class ExecuteRequest(BaseModel):
    task_id: str
    task_type: str = ""


@app.get("/health")
def health() -> dict:
    return {
        "ok": (not ORIGINAL_RUNTIME_ENABLED) or _boot_error is None,
        "booted": _booted,
        "running": sorted(_running),
        "original_runtime_enabled": ORIGINAL_RUNTIME_ENABLED,
        "original_app_path": str(ORIGINAL_APP_PATH),
        "error": _boot_error,
    }


@app.post("/execute")
def execute(req: ExecuteRequest, background: BackgroundTasks, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    if not req.task_id.strip():
        raise HTTPException(status_code=400, detail="task_id is required")
    if not req.task_type.startswith("sunny_"):
        if not ORIGINAL_RUNTIME_ENABLED:
            raise HTTPException(status_code=503, detail={"message": "original runtime is disabled; only sunny_* tasks are available"})
        if _boot_error is not None:
            raise HTTPException(status_code=503, detail={"message": "worker bootstrap failed", "error": _boot_error})
        bootstrap_original_runtime()
    with _state_lock:
        if req.task_id in _running:
            return {"ok": True, "accepted": False, "already_running": True, "task_id": req.task_id}
        _running.add(req.task_id)
    background.add_task(_run_task, req.task_id, req.task_type)
    return {"ok": True, "accepted": True, "task_id": req.task_id, "task_type": req.task_type}


def _run_task(task_id: str, task_type: str = "") -> None:
    try:
        if task_type.startswith("sunny_"):
            from sunny_runner import run_sunny_task
            run_sunny_task(task_id)
            return
        bootstrap_original_runtime()
        from application.tasks import append_task_event, execute_task

        append_task_event(task_id, "Python 自动化 Worker 已接管任务", event_type="state", level="info", detail={"worker": "python"})
        execute_task(task_id)
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[worker] task {task_id} failed:\n{tb}", flush=True)
        try:
            from application.tasks import TASK_STATUS_FAILED, TaskLogger

            TaskLogger(task_id).finish(TASK_STATUS_FAILED, error=f"Python Worker 执行失败: {exc}", result={"traceback": tb[-4000:]})
        except Exception:
            pass
    finally:
        with _state_lock:
            _running.discard(task_id)


