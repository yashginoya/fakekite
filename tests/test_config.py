"""Milestone 0: config loads and validates, and the app boots a health route."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fakekite.app import create_app
from fakekite.config import Config, ConfigError, load_config


def test_example_config_loads(config: Config) -> None:
    assert config.server.rest_port == 8000
    assert config.auth.mode == "accept_any"
    assert config.market.seed == 42
    tokens = {i.token for i in config.market.instruments}
    assert {256265, 408065, 779521, 412675} <= tokens
    # is_index honoured for the index instrument.
    by_token = {i.token: i for i in config.market.instruments}
    assert by_token[256265].is_index is True
    assert by_token[408065].is_index is False
    # segment defaults to exchange when omitted.
    assert by_token[256265].segment == "NSE"


def test_unknown_key_rejected(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "market:\n  seed: 1\n  instruments:\n    - token: 1\n      symbol: X\n"
        "      exchange: NSE\n      start_price: 10\nbogus: 1\n"
    )
    with pytest.raises(ConfigError) as exc:
        load_config(bad)
    assert "bogus" in str(exc.value)


def test_missing_file() -> None:
    with pytest.raises(ConfigError):
        load_config("/no/such/config.yaml")


def test_health_route(config: Config) -> None:
    client = TestClient(create_app(config))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
