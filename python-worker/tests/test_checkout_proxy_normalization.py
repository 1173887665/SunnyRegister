from __future__ import annotations

import ast
from pathlib import Path


def test_pay153_normalizer_handles_kookeey_as_socks5h() -> None:
    app_path = Path(__file__).parents[1] / "tools" / "pay153_checkout" / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    normalize_proxy = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_proxy"
    )
    module = ast.Module(body=[normalize_proxy], type_ignores=[])
    namespace = {
        "Any": object,
        "quote": __import__("urllib.parse", fromlist=["quote"]).quote,
        "unquote": __import__("urllib.parse", fromlist=["unquote"]).unquote,
        "urlsplit": __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit,
    }
    exec(compile(module, str(app_path), "exec"), namespace)

    proxy = namespace["normalize_proxy"](
        "gate.kookeey.info:1000:user:password-DE-session"
    )

    assert proxy == "socks5h://user:password-DE-session@gate.kookeey.info:1000"
