from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field


def _secret_value(env_key: str, file_key: str) -> str:
    file_name = os.getenv(file_key, "").strip()
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.getenv(env_key, "").strip()


WORKER_TOKEN = _secret_value("PYTHON_WORKER_TOKEN", "PYTHON_WORKER_TOKEN_FILE")
app = FastAPI(title="SunnyRegister Link Workbench Worker", version="1.0.0")


class CheckoutRequest(BaseModel):
    token: str
    checkout_proxies: list[str]
    promotion_proxies: list[str]
    proxy_slot: int = 0
    checkout_kind: str = "unknown"
    plan: str = "plus"
    link_type: str = "hosted"
    country: str = "US"
    currency: str = "USD"
    retry_count: int = 3
    use_promo: bool = True
    promo_campaign: str = ""
    promo_country: str = ""
    promo_code: str = ""
    workspace_name: str = ""
    workspace_id: str = ""
    seat_quantity: int = 5
    price_interval: str = "month"
    credit_quantity: int = 13
    ideal_bank: str = ""
    pix_tax_id: str = ""
    pix_auto_kind: str = "cpf"
    checkout_mode: str = "auto"
    chain_config: dict = Field(default_factory=dict)


def _check_token(authorization: str | None) -> None:
    if WORKER_TOKEN and authorization != f"Bearer {WORKER_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized worker token")


@app.get("/health")
def health(authorization: str | None = Header(default=None)) -> dict:
    # Keep readiness probes unauthenticated, matching the primary Worker.
    # Job and cancellation endpoints still require the configured token.
    from tools.pay153_checkout.workbench_adapter import runtime_snapshot

    return {
        "ok": True,
        "service": "link-workbench-worker",
        "runtime": "workbench",
        **runtime_snapshot(),
    }


@app.post("/checkout/jobs")
def start_checkout(req: CheckoutRequest, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    from tools.pay153_checkout.workbench_adapter import start_checkout as run_checkout

    return {"ok": True, "job_id": run_checkout(req.model_dump())}


@app.get("/checkout/jobs/{job_id}")
def checkout_job(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    from tools.pay153_checkout.workbench_adapter import checkout_status

    result = checkout_status(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="workbench checkout job not found")
    return result


@app.post("/checkout/jobs/{job_id}/cancel")
def cancel_checkout_job(job_id: str, authorization: str | None = Header(default=None)) -> dict:
    _check_token(authorization)
    from tools.pay153_checkout.workbench_adapter import cancel_checkout

    return {"ok": cancel_checkout(job_id), "job_id": job_id}


def _gcash_response(order_id: str, suffix: str, method: str, body: bytes = b"", query: str = "", authorization: str | None = None) -> Response:
    """Run the existing GCash handler inside this worker's isolated process."""
    _check_token(authorization)
    from tools.pay153_checkout import workbench_adapter as adapter

    path = f"/api/gcash/orders/{order_id}{suffix}"
    with adapter.engine_app.app.test_client() as client:
        result = client.open(path, method=method, data=body, query_string=query)
        headers = {key: value for key, value in result.headers.items() if key.lower() in {"content-type", "cache-control"}}
        return Response(content=result.get_data(), status_code=result.status_code, headers=headers, media_type=None)


@app.get("/api/gcash/orders/{order_id}")
def gcash_order(order_id: str, request: Request, authorization: str | None = Header(default=None)) -> Response:
    return _gcash_response(order_id, "", "GET", query=str(request.query_params), authorization=authorization)


@app.post("/api/gcash/orders/{order_id}/qr")
async def gcash_order_qr(order_id: str, request: Request, authorization: str | None = Header(default=None)) -> Response:
    return _gcash_response(order_id, "/qr", "POST", body=await request.body(), query=str(request.query_params), authorization=authorization)


@app.post("/api/gcash/orders/{order_id}/callback")
async def gcash_order_callback(order_id: str, request: Request, authorization: str | None = Header(default=None)) -> Response:
    return _gcash_response(order_id, "/callback", "POST", body=await request.body(), query=str(request.query_params), authorization=authorization)
