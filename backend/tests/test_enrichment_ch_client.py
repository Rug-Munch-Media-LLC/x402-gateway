"""Regression: ``_ch()`` must return the ClickHouse client, not ``None``.

The original bug omitted ``return``, so every ClickHouse enrichment path got
``None`` and died with AttributeError on the next attribute access.
"""

import sys
import types

from app.routers import x402_enrichment


def test_ch_returns_client_instance(monkeypatch):
    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        return "CH-CLIENT"

    monkeypatch.setitem(
        sys.modules, "clickhouse_driver", types.SimpleNamespace(Client=fake_client)
    )

    client = x402_enrichment._ch()

    assert client == "CH-CLIENT", "_ch() must return the Client, not None"
    assert created["host"], "connection kwargs must be forwarded"
    assert created["settings"] == {"max_execution_time": 3}


def test_ch_forwards_password_none_when_unset(monkeypatch):
    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        return "CH-CLIENT"

    monkeypatch.setitem(
        sys.modules, "clickhouse_driver", types.SimpleNamespace(Client=fake_client)
    )
    monkeypatch.delenv("CH_PASSWORD", raising=False)

    x402_enrichment._ch()

    assert created["password"] is None
