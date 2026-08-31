"""Small worker facade used by the standalone MoMo task manager."""
from __future__ import annotations

from typing import Any, Callable

from .momo_protocol import MobileWalletProvider


class MomoTaskWorker:
    def __init__(self, provider: MobileWalletProvider, log: Callable[[str], None] | None = None) -> None:
        self.provider = provider
        self.log = log or (lambda _message: None)

    def run(self, operation: str, payload: dict[str, Any]) -> bool:
        self.log(f"MoMo provider operation: {operation}")
        result = self.provider.execute(operation, payload)
        if not result.ok:
            self.log(result.error or f"MoMo {operation} failed")
        return result.ok
