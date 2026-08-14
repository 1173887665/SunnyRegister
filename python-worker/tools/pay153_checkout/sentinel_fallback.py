from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


SentinelFactory = Callable[..., Awaitable[dict[str, str]]]


def resolve_payment_sentinel_headers(
    factory: SentinelFactory,
    proxy: str,
    flow: str,
    device_id: str,
    did: str,
    *,
    use_sen: bool = True,
    use_so: bool = True,
    allow_fallback: bool = False,
    log: Callable[[str], None] = lambda _message: None,
) -> dict[str, str]:
    try:
        return asyncio.run(factory(
            proxy, flow, device_id, did, use_sen=use_sen, use_so=use_so,
        ))
    except RuntimeError as exc:
        message = str(exc)
        fallbackable = message.startswith("Sentinel token generation failed") or message.startswith("Sentinel Node VM")
        if not allow_fallback or not fallbackable:
            raise
        log(
            "PayPal Sentinel 完整证明未生成，按参考流程降级为不携带 Sentinel 头继续请求："
            + message.split(":", 1)[-1].strip()[:180]
        )
        return {}
