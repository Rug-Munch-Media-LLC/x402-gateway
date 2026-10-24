"""Import smoke tests for the gateway routers.

The gateway ships ``app/routers`` and ``app/domain`` only; the host application
supplies ``app.core``. These tests bind the checkout's own package
(``conftest.py`` does the same) and stub **only** that host edge, so a missing
symbol inside our modules fails the build instead of hiding behind a harness
stub.

Regression: ``x402_alpha_revenue_tools`` annotated its copy-trade handler with
``WhaleCopyTradeRequest`` without ever defining it. Annotations are evaluated
at ``def`` time (the module has no ``from __future__ import annotations``), so
the whole router raised ``NameError`` on import.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
ROUTER_MODULES = (
    "x402_alpha_revenue_tools",
    "x402_alpha_tools",
    "x402_premium_tools",
    "x402_enrichment",
)


def _bind_host_edge() -> None:
    """Provide the ``app.core.redis`` edge that the host application ships."""
    if "app" not in sys.modules:  # conftest normally binds it already
        package = types.ModuleType("app")
        package.__path__ = [str(APP_DIR)]
        sys.modules["app"] = package
    if "app.core" not in sys.modules:
        core = types.ModuleType("app.core")
        core.__path__ = []
        sys.modules["app.core"] = core
    if "app.core.redis" not in sys.modules:
        redis_module = types.ModuleType("app.core.redis")
        redis_module.get_redis = lambda *args, **kwargs: None
        sys.modules["app.core.redis"] = redis_module


@pytest.mark.parametrize("name", ROUTER_MODULES)
def test_router_module_imports(name: str) -> None:
    """Every shipped router must import without harness-level symbol stubs."""
    _bind_host_edge()
    module = importlib.import_module(f"app.routers.{name}")
    assert Path(module.__file__).resolve().parent == (APP_DIR / "routers").resolve()


def test_whale_copy_trade_request_is_defined() -> None:
    """The annotation on ``whale_copy_trade`` must resolve to a real model."""
    _bind_host_edge()
    module = importlib.import_module("app.routers.x402_alpha_revenue_tools")
    model = getattr(module, "WhaleCopyTradeRequest", None)
    assert model is not None, "whale_copy_trade annotates WhaleCopyTradeRequest"
    assert issubclass(model, BaseModel)
    assert module.whale_copy_trade.__annotations__["req"] is model


def test_whale_copy_trade_request_contract() -> None:
    """Defaults and bounds the handler relies on (``req.address.lower()`` etc.)."""
    _bind_host_edge()
    module = importlib.import_module("app.routers.x402_alpha_revenue_tools")
    model = module.WhaleCopyTradeRequest

    request = model(address="0xAbC")
    assert request.address == "0xAbC"
    assert request.chain == "solana"
    assert request.lookback_hours == 24
    assert request.min_trade_usd == 0.0

    override = model(address="0xAbC", chain="base", lookback_hours=6, min_trade_usd=500)
    assert (override.chain, override.lookback_hours) == ("base", 6)
    assert override.min_trade_usd == 500

    with pytest.raises(ValidationError):
        model(address="0xAbC", lookback_hours=0)
    with pytest.raises(ValidationError):
        model(address="0xAbC", min_trade_usd=-1)
