"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI

from fakekite.app import create_app
from fakekite.config import Config, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]

# Valid auth header for accept_any mode.
AUTH_HEADER = {"Authorization": "token testkey:testtoken"}


def _load() -> Config:
    return load_config(REPO_ROOT / "config.example.yaml")


@pytest.fixture
def config() -> Config:
    return _load()


@pytest.fixture
def app_factory() -> Callable[..., FastAPI]:
    """Build an app, optionally overriding latency fields for fast/slow tests."""

    def make(**latency_overrides) -> FastAPI:
        cfg = _load()
        if latency_overrides:
            data = cfg.model_dump()
            data["latency"].update(latency_overrides)
            cfg = Config.model_validate(data)
        return create_app(cfg)

    return make
