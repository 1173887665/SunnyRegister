from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel

os.environ.setdefault("PYTHONUTF8", "1")


def _default_account_manager_db_url() -> str:
    """Return a safe local default DB path.

    Docker images provide ACCOUNT_MANAGER_DATABASE_URL explicitly as /app/data.
    In local development this file lives under <repo>/python-worker/worker.py, so
    the matching Go backend database is <repo>/data/account_manager.db.
    """
    worker_file = Path(__file__).resolve()
    if worker_file.parent.name == "python-worker":
        return "sqlite:///" + str((worker_file.parent.parent / "data" / "account_manager.db")).replace("\\", "/")
    return "sqlite:////app/data/account_manager.db"


os.environ.setdefault("ACCOUNT_MANAGER_DATABASE_URL", _default_account_manager_db_url())

def _secret_value(env_key: str, file_key: str) -> str:
    file_name = os.getenv(file_key, "").strip()
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.getenv(env_key, "").strip()


WORKER_TOKEN = _secret_value("PYTHON_WORKER_TOKEN", "PYTHON_WORKER_TOKEN_FILE")

app = FastAPI(title="SunnyRegister Python Automation Worker", version="1.0.0")
_state_lock = threading.Lock()
_running: set[str] = set()


def _check_token(auth: str | None) -> None:
    if not WORKER_TOKEN:
        return
    expected = f"Bearer {WORKER_TOKEN}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized worker token")


@app.on_event("startup")
def on_startup() -> None:
    print("[worker] SunnyRegister automation worker ready", flush=True)


class ExecuteRequest(BaseModel):
    task_id: str
    task_type: str = ""


@app.get("/health")
def health() -> dict:
    sunny_db_path = ""
    sunny_db_error = ""
    try:
        from sunny_core.db import db_path

        sunny_db_path = str(Path(db_path()).resolve())
    except Exception as exc:
        sunny_db_error = str(exc)
    return {
        "ok": sunny_db_error == "",
        "running": sorted(_running),
        "cwd": os.getcwd(),
        "python": sys.executable,
        "sunny_db_path": sunny_db_path,
        "sunny_db_error": sunny_db_error,
    }


@app.post("/execute")
def execute(req: ExecuteRequest, background: BackgroundTasks, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    if not req.task_id.strip():
        raise HTTPException(status_code=400, detail="task_id is required")
    if not req.task_type.startswith("sunny_"):
        raise HTTPException(status_code=400, detail="only sunny_* task types are supported")
    with _state_lock:
        if req.task_id in _running:
            return {"ok": True, "accepted": False, "already_running": True, "task_id": req.task_id}
        _running.add(req.task_id)
    background.add_task(_run_task, req.task_id, req.task_type)
    return {"ok": True, "accepted": True, "task_id": req.task_id, "task_type": req.task_type}


def _run_task(task_id: str, task_type: str = "") -> None:
    try:
        if not task_type.startswith("sunny_"):
            raise RuntimeError(f"unsupported task type: {task_type}")
        from sunny_runner import run_sunny_task

        run_sunny_task(task_id)
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[worker] task {task_id} failed:\n{tb}", flush=True)
        _finish_sunny_task_failed(task_id, exc, tb)
    finally:
        with _state_lock:
            _running.discard(task_id)


def _finish_sunny_task_failed(task_id: str, exc: Exception, tb: str) -> None:
    try:
        from sunny_core.db import SunnyDB, db_path, now_sql

        db = SunnyDB(task_id)
        try:
            message = f"SunnyRegister Worker 启动任务失败: {exc}"
            detail = {"traceback": tb[-4000:], "worker_db_path": str(Path(db_path()).resolve())}
            db.update_task(status="failed", error=message, result_json='{"error":"worker failed before startup"}', finished_at=now_sql())
            db.event(message, "error", detail=detail)
        finally:
            db.close()
    except Exception as inner:
        print(f"[worker] failed to write SunnyRegister failure to DB: {inner}", flush=True)


