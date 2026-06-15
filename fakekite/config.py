"""Pydantic config models + loader/validator for FakeKite.

The whole of ``config.example.yaml`` maps onto :class:`Config`. ``load_config``
reads a YAML file and raises ``ConfigError`` with a readable message on any
validation failure (so a typo in the config surfaces clearly at startup, not as
a stack trace deep inside a handler).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(Exception):
    """Raised when the config file is missing, unparseable, or invalid."""


class _Model(BaseModel):
    # Reject unknown keys so config typos are caught instead of silently ignored.
    # validate_assignment lets the control plane mutate fields at runtime (e.g.
    # latency.rest_ms, orders.default_state) with the same validation as load.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ServerConfig(_Model):
    rest_host: str = "127.0.0.1"
    rest_port: int = Field(default=8000, ge=1, le=65535)
    ws_path: str = "/ws"

    @model_validator(mode="after")
    def _check_ws_path(self) -> ServerConfig:
        if not self.ws_path.startswith("/"):
            raise ValueError("server.ws_path must start with '/'")
        return self


class AuthConfig(_Model):
    mode: Literal["accept_any", "fixed"] = "accept_any"
    api_key: str | None = None
    access_token: str | None = None

    @model_validator(mode="after")
    def _check_fixed(self) -> AuthConfig:
        if self.mode == "fixed" and (not self.api_key or not self.access_token):
            raise ValueError("auth.mode 'fixed' requires both auth.api_key and auth.access_token")
        return self


class OrdersConfig(_Model):
    default_state: Literal["COMPLETE", "OPEN", "REJECTED"] = "COMPLETE"
    reject_message: str = (
        "Insufficient funds. Required margin is 95417.84 but available margin is 74251.80."
    )
    emit_order_history: bool = True


class LatencyConfig(_Model):
    rest_ms: int = Field(default=0, ge=0)
    rest_jitter_ms: int = Field(default=0, ge=0)
    ws_tick_interval_ms: int = Field(default=1000, ge=1)
    ws_push_delay_ms: int = Field(default=0, ge=0)
    order_update_delay_ms: int = Field(default=0, ge=0)
    heartbeat_interval_ms: int = Field(default=2000, ge=1)


class RateLimitConfig(_Model):
    enabled: bool = False


class InstrumentConfig(_Model):
    token: int = Field(ge=1)
    symbol: str
    exchange: Literal["NSE", "BSE", "NFO", "CDS", "BCD", "MCX"]
    segment: str | None = None
    is_index: bool = False
    tick_size: float = Field(default=0.05, gt=0)
    lot_size: int = Field(default=1, ge=1)
    start_price: float = Field(gt=0)
    volatility: float = Field(default=0.0015, ge=0)

    @model_validator(mode="after")
    def _default_segment(self) -> InstrumentConfig:
        # Segment defaults to the exchange when not given (matches Kite's CSV dump).
        if self.segment is None:
            self.segment = self.exchange
        return self


class MarketConfig(_Model):
    seed: int = 42
    instruments: list[InstrumentConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_tokens(self) -> MarketConfig:
        tokens = [i.token for i in self.instruments]
        dupes = {t for t in tokens if tokens.count(t) > 1}
        if dupes:
            raise ValueError(f"market.instruments has duplicate token(s): {sorted(dupes)}")
        symbols = [(i.exchange, i.symbol) for i in self.instruments]
        sdupes = {s for s in symbols if symbols.count(s) > 1}
        if sdupes:
            raise ValueError(
                f"market.instruments has duplicate exchange:symbol pair(s): {sorted(sdupes)}"
            )
        return self


class Config(_Model):
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    orders: OrdersConfig = Field(default_factory=OrdersConfig)
    latency: LatencyConfig = Field(default_factory=LatencyConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    market: MarketConfig


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config file, raising ``ConfigError`` on failure."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse YAML in {p}: {exc}") from exc
    if raw is None:
        raise ConfigError(f"config file is empty: {p}")
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        lines = [f"invalid config in {p}:"]
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "<root>"
            lines.append(f"  - {loc}: {err['msg']}")
        raise ConfigError("\n".join(lines)) from exc
