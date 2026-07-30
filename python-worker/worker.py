from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
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
# A browser driver can occasionally remain busy after its browser disconnects. The
# worker already runs each task in a dedicated process; reclaim an inactive child
# after 15 minutes so its browser, IMAP sockets and memory cannot leak forever.
TASK_IDLE_TIMEOUT_SECONDS = max(60, int(os.getenv("SUNNY_TASK_IDLE_TIMEOUT_SECONDS", "900")))
TASK_WATCH_INTERVAL_SECONDS = max(5, int(os.getenv("SUNNY_TASK_WATCH_INTERVAL_SECONDS", "15")))

app = FastAPI(title="SunnyRegister Python Automation Worker", version="1.0.0")
_state_lock = threading.Lock()
_running: set[str] = set()
_processes: dict[str, subprocess.Popen] = {}


def _check_token(auth: str | None) -> None:
    if not WORKER_TOKEN:
        return
    expected = f"Bearer {WORKER_TOKEN}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Unauthorized worker token")


@app.on_event("startup")
def on_startup() -> None:
    # Do not import or validate Playwright/Camoufox here. Browser automation is
    # lazy-loaded by the isolated task subprocess only when a task is accepted.
    print("[worker] SunnyRegister automation worker ready (browser lazy loading enabled)", flush=True)


class ExecuteRequest(BaseModel):
    task_id: str
    task_type: str = ""


class CancelRequest(BaseModel):
    task_id: str


class ProbeAccessTokenRequest(BaseModel):
    access_token: str
    proxy_url: str = ""


@app.get("/health")
def health() -> dict:
    with _state_lock:
        for task_id, process in list(_processes.items()):
            if process.poll() is not None:
                _processes.pop(task_id, None)
                _running.discard(task_id)
        running = sorted(_running)
    sunny_db_path = ""
    sunny_db_error = ""
    try:
        from sunny_core.db import db_path

        sunny_db_path = str(Path(db_path()).resolve())
    except Exception as exc:
        sunny_db_error = str(exc)
    return {
        "ok": sunny_db_error == "",
        "running": running,
        "cwd": os.getcwd(),
        "python": sys.executable,
        "sunny_db_path": sunny_db_path,
        "sunny_db_error": sunny_db_error,
        "task_isolation": "subprocess",
        "task_idle_timeout_seconds": TASK_IDLE_TIMEOUT_SECONDS,
    }


@app.post("/execute")
def execute(req: ExecuteRequest, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    if not req.task_id.strip():
        raise HTTPException(status_code=400, detail="task_id is required")
    if not req.task_type.startswith("sunny_"):
        raise HTTPException(status_code=400, detail="only sunny_* task types are supported")
    with _state_lock:
        if req.task_id in _running:
            return {"ok": True, "accepted": False, "already_running": True, "task_id": req.task_id}
        process = _start_task_process(req.task_id)
        _running.add(req.task_id)
        _processes[req.task_id] = process
    threading.Thread(
        target=_watch_task_process,
        args=(req.task_id, process),
        name=f"sunny-task-watch-{req.task_id}",
        daemon=True,
    ).start()
    return {"ok": True, "accepted": True, "task_id": req.task_id, "task_type": req.task_type}


@app.post("/cancel")
def cancel(req: CancelRequest, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    task_id = req.task_id.strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    _set_cancel_requested(task_id)
    with _state_lock:
        process = _processes.get(task_id)
    forced = False
    if process and process.poll() is None:
        deadline = time.monotonic() + 1.0
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is None:
            forced = True
            _terminate_process_tree(process)
    summary = _mark_task_cancelled(task_id)
    with _state_lock:
        _running.discard(task_id)
        if _processes.get(task_id) is process:
            _processes.pop(task_id, None)
    return {"ok": True, "task_id": task_id, "cancelled": True, "forced": forced, **summary}


@app.post("/probe-access-token")
def probe_access_token(req: ProbeAccessTokenRequest, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    from sunny_core.access_token_probe import probe_access_token as run_probe

    return run_probe(req.access_token, req.proxy_url)


@app.on_event("shutdown")
def on_shutdown() -> None:
    with _state_lock:
        processes = list(_processes.values())
        _processes.clear()
        _running.clear()
    for process in processes:
        if process.poll() is None:
            _terminate_process_tree(process)


def _start_task_process(task_id: str) -> subprocess.Popen:
    worker_dir = Path(__file__).resolve().parent
    kwargs: dict = {
        "cwd": str(worker_dir),
        "env": os.environ.copy(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([sys.executable, "-m", "sunny_runner", task_id], **kwargs)


def _watch_task_process(task_id: str, process: subprocess.Popen) -> None:
    last_signature: tuple[str, str, str] | None = None
    last_activity = time.monotonic()
    reclaimed_reason = ""
    while process.poll() is None:
        try:
            from sunny_core.db import SunnyDB

            db = SunnyDB(task_id, ensure_schema=False)
            try:
                task = db.task()
                signature = (str(task.get("updated_at") or ""), str(task.get("progress_current") or ""), str(task.get("status") or ""))
            finally:
                db.close()
            if signature != last_signature:
                last_signature = signature
                last_activity = time.monotonic()
        except Exception:
            # SQLite may briefly be locked by the task; avoid false-positive cleanup.
            last_activity = time.monotonic()
        if time.monotonic() - last_activity >= TASK_IDLE_TIMEOUT_SECONDS:
            reclaimed_reason = f"Python 自动化任务连续 {TASK_IDLE_TIMEOUT_SECONDS // 60} 分钟无状态更新，已自动终止卡死子进程并释放浏览器/邮件资源"
            print(f"[worker] reclaiming stalled task {task_id}", flush=True)
            _terminate_process_tree(process)
            break
        time.sleep(TASK_WATCH_INTERVAL_SECONDS)

    return_code = process.wait()
    with _state_lock:
        if _processes.get(task_id) is process:
            _processes.pop(task_id, None)
        _running.discard(task_id)
    try:
        from sunny_core.db import SunnyDB, now_sql

        db = SunnyDB(task_id)
        try:
            status = db.task_status()
            if status == "cancel_requested":
                db.mark_cancelled("用户已停止注册任务")
            elif return_code != 0 and status not in {"succeeded", "failed", "cancelled", "interrupted"}:
                message = reclaimed_reason or f"SunnyRegister Worker 子进程异常退出，退出码 {return_code}"
                summary = db.fail_unfinished_mailboxes(message)
                db.update_task(
                    status="failed",
                    error=message,
                    progress_current=summary["completed"] + summary["failed"],
                    success_count=summary["completed"],
                    error_count=summary["failed"],
                    finished_at=now_sql(),
                )
                db.event(message, "error", detail={"return_code": return_code})
        finally:
            db.close()
    except Exception as exc:
        print(f"[worker] failed to reconcile child task {task_id}: {exc}", flush=True)


def _set_cancel_requested(task_id: str) -> None:
    try:
        from sunny_core.db import SunnyDB

        db = SunnyDB(task_id)
        try:
            if db.task_status() not in {"succeeded", "failed", "cancelled", "interrupted"}:
                db.update_task(status="cancel_requested")
        finally:
            db.close()
    except Exception as exc:
        print(f"[worker] failed to set cancel_requested for {task_id}: {exc}", flush=True)


def _mark_task_cancelled(task_id: str) -> dict:
    try:
        from sunny_core.db import SunnyDB

        db = SunnyDB(task_id)
        try:
            return db.mark_cancelled("用户已停止注册任务")
        finally:
            db.close()
    except Exception as exc:
        print(f"[worker] failed to finalize cancelled task {task_id}: {exc}", flush=True)
        return {"completed": 0, "failed": 0, "completed_mailbox_ids": [], "failed_mailbox_ids": []}


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait(timeout=3)
    except Exception:
        pass


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

