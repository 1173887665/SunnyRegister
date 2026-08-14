from __future__ import annotations

from typing import Any


_LEGACY_SHARED_PROVIDERS = {"pix", "momo"}
_LEGACY_CHECKOUT_EXIT_PROVIDERS = {"paypal", "upi", "ideal", "twint"}


def shares_checkout_proxy(options: dict[str, Any], provider: str) -> bool:
    return provider in _LEGACY_SHARED_PROVIDERS and not bool(options.get("named_proxy_pools"))


def checkout_route_proxy(
    options: dict[str, Any],
    provider: str,
    promotion_proxy: str,
    checkout_proxy: str,
) -> str:
    if options.get("named_proxy_pools") or provider in _LEGACY_CHECKOUT_EXIT_PROVIDERS:
        return checkout_proxy
    return promotion_proxy


def promotion_route_proxy(
    options: dict[str, Any],
    provider: str,
    promotion_proxy: str,
    checkout_proxy: str,
) -> str:
    if options.get("named_proxy_pools") or provider != "gcash":
        return promotion_proxy
    return checkout_proxy
