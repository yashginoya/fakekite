"""ASGI app: owns shared state and wires up REST + WS.

For Milestone 0 this exposes a single health route. Later milestones attach the
REST routers, the WebSocket endpoint and the control plane onto the same app,
all sharing the one :class:`AppState` stored on ``app.state.fk``.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from .config import Config
from .control import router as control_router
from .latency import rest_latency_dependency
from .rest.envelopes import install_exception_handlers
from .rest.market import router as market_router
from .rest.orders import router as orders_router
from .rest.portfolio import router as portfolio_router
from .rest.ratelimit import RateLimitMiddleware
from .rest.session import router as session_router
from .state import AppState
from .ws.ticker import ticker_endpoint


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="FakeKite", version="0.1.0")
    app.state.fk = AppState(config=config)

    install_exception_handlers(app)
    app.add_middleware(RateLimitMiddleware, get_state=lambda: app.state.fk)

    @app.get("/")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "fakekite"}

    # Configured REST latency is applied before every Kite handler.
    kite_latency = [Depends(rest_latency_dependency)]
    app.include_router(orders_router, dependencies=kite_latency)
    app.include_router(market_router, dependencies=kite_latency)
    app.include_router(session_router, dependencies=kite_latency)
    app.include_router(portfolio_router, dependencies=kite_latency)
    # Control plane: no Kite auth, no artificial latency.
    app.include_router(control_router)
    app.add_api_websocket_route(config.server.ws_path, ticker_endpoint)

    return app
