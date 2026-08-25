import ast
from pathlib import Path

from tools.pay153_checkout.proxy_routing import (
    checkout_route_proxy,
    promotion_route_proxy,
    shares_checkout_proxy,
)


PROVIDERS = (
    "hosted", "paypal", "pix", "upi", "momo", "gcash", "gopay",
    "ideal", "twint", "kakao", "ph_short",
)


def test_named_pools_keep_checkout_and_promotion_roles_for_every_provider() -> None:
    options = {"named_proxy_pools": True}

    for provider in PROVIDERS:
        assert checkout_route_proxy(options, provider, "promotion-proxy", "checkout-proxy") == "checkout-proxy"
        assert promotion_route_proxy(options, provider, "promotion-proxy", "checkout-proxy") == "promotion-proxy"
        assert not shares_checkout_proxy(options, provider)


def test_reference_mode_preserves_legacy_special_routes() -> None:
    options = {"named_proxy_pools": False}

    assert shares_checkout_proxy(options, "pix")
    assert shares_checkout_proxy(options, "momo")
    assert checkout_route_proxy(options, "hosted", "entry", "exit") == "entry"
    assert checkout_route_proxy(options, "paypal", "entry", "exit") == "exit"
    assert checkout_route_proxy(options, "gopay", "entry", "exit") == "exit"
    assert promotion_route_proxy(options, "gcash", "entry", "exit") == "exit"


def test_job_runner_does_not_shadow_proxy_routing_helpers() -> None:
    app_path = Path(__file__).parents[1] / "tools" / "pay153_checkout" / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    shadowed = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
        and node.id in {"checkout_route_proxy", "promotion_route_proxy"}
    }

    assert shadowed == set()
