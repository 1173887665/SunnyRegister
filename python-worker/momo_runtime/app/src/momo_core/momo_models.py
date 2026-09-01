"""Data contracts for the standalone MoMo runtime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MomoProfile:
    phone: str
    display_name: str = ""
    email: str = ""
    date_of_birth: str = ""
    address: str = ""
    country: str = "VN"
    skip_kyc: bool = True


@dataclass(frozen=True)
class MomoQrOrder:
    payload: str
    amount: str = ""
    currency: str = "VND"


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    data: dict[str, Any]
    error: str = ""
