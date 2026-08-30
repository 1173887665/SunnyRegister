from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


SRC_DIR = Path(__file__).parents[1] / "gopay_runtime" / "app" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from opai.core import payment_inbox


SNAP_URL = "https://app.midtrans.com/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174000"


def _response(status_code: int = 200):
    return SimpleNamespace(
        status_code=status_code,
        text="{}",
        json=lambda: {
            "transaction_details": {"order_id": "setatt_test", "gross_amount": "1", "currency": "IDR"},
            "merchant": {"client_key": "client-test"},
        },
    )


def test_midtrans_meta_retries_unexpected_eof_then_succeeds(monkeypatch):
    calls = []

    class Session:
        def __init__(self, **kwargs):
            pass

        def get(self, *args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise RuntimeError("unexpected EOF")
            return _response()

    monkeypatch.setitem(sys.modules, "tls_client", ModuleType("tls_client"))
    sys.modules["tls_client"].Session = Session
    monkeypatch.setenv("OPAI_MIDTRANS_META_RETRIES", "3")
    with patch.object(payment_inbox.time, "sleep") as sleep:
        result = payment_inbox._midtrans_transaction_meta(SNAP_URL)

    assert result["order_id"] == "setatt_test"
    assert len(calls) == 2
    sleep.assert_called_once()


def test_midtrans_meta_exhausted_transport_error_is_readable(monkeypatch):
    calls = []

    class Session:
        def __init__(self, **kwargs):
            pass

        def get(self, *args, **kwargs):
            calls.append(1)
            raise RuntimeError("unexpected EOF")

    monkeypatch.setitem(sys.modules, "tls_client", ModuleType("tls_client"))
    sys.modules["tls_client"].Session = Session
    monkeypatch.setenv("OPAI_MIDTRANS_META_RETRIES", "2")
    with patch.object(payment_inbox.time, "sleep"):
        try:
            payment_inbox._midtrans_transaction_meta(SNAP_URL)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected transport failure")

    assert len(calls) == 2
    assert "网络连接中断" in message
    assert "已重试 2 次" in message


def test_midtrans_meta_http_error_is_not_retried(monkeypatch):
    calls = []

    class Session:
        def __init__(self, **kwargs):
            pass

        def get(self, *args, **kwargs):
            calls.append(1)
            return _response(404)

    monkeypatch.setitem(sys.modules, "tls_client", ModuleType("tls_client"))
    sys.modules["tls_client"].Session = Session
    with patch.object(payment_inbox.time, "sleep"):
        try:
            payment_inbox._midtrans_transaction_meta(SNAP_URL)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected HTTP failure")

    assert len(calls) == 1
    assert "404" in message
