"""Guard: a function-local `import json` makes json local for the whole function scope."""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers" / "x402_databus_tools.py"


def test_no_function_local_json_shadow() -> None:
    source = SRC.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        local = any(
            isinstance(n, ast.Import) and any(a.name == "json" for a in n.names)
            for n in ast.walk(node)
        )
        if not local:
            continue
        unit = ast.Module(body=[node], type_ignores=[])
        codes = [c for c in compile(unit, str(SRC), "exec").co_consts if hasattr(c, "co_varnames")]
        fn_code = codes[0]
        assert "json" not in fn_code.co_varnames, (
            f"{node.name}() line {node.lineno}: function-local `import json` makes json a local, "
            "so the earlier cache-hit `json.loads(cached_result)` raises UnboundLocalError"
        )
