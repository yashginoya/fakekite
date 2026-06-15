"""Async delay helpers. Every artificial delay in FakeKite goes through here.

Uses ``asyncio.sleep`` exclusively — never ``time.sleep`` — so delays yield the
single event loop instead of stalling every connected client. Latency values are
read live from ``LatencyConfig`` so the ``/_control/latency`` endpoint (Milestone
4) can change them without a restart.
"""

from __future__ import annotations

import asyncio
import random

from fastapi import Request

from .config import LatencyConfig


async def sleep_ms(ms: float) -> None:
    """Sleep for ``ms`` milliseconds (no-op for non-positive values)."""
    if ms and ms > 0:
        await asyncio.sleep(ms / 1000.0)


async def rest_delay(cfg: LatencyConfig) -> None:
    """Apply the base REST latency plus uniform jitter in ``[0, rest_jitter_ms]``."""
    delay = cfg.rest_ms
    if cfg.rest_jitter_ms > 0:
        delay += random.uniform(0, cfg.rest_jitter_ms)
    await sleep_ms(delay)


async def rest_latency_dependency(request: Request) -> None:
    """Router dependency that applies the current REST latency before each handler.

    Reads ``config.latency`` live, so ``/_control/latency`` changes take effect
    without a restart.
    """
    await rest_delay(request.app.state.fk.config.latency)
