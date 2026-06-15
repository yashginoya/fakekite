"""Optional per-endpoint rate limiting (KITE_SPEC.md §1). Default OFF.

When ``rate_limit.enabled`` is true, a simple fixed-window-per-second counter is
kept per (api_key, endpoint-class). Exceeding the documented limit returns HTTP
429 with the error envelope so the terminal's backoff logic can be exercised.
Kept deliberately minimal — this is a test knob, not a production limiter.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .envelopes import error_response

# Per-second limits by endpoint class (KITE_SPEC.md §1 rate-limit table).
_LIMITS = {
    "quote": 1,
    "order_place": 10,
    "other": 10,
}


def _endpoint_class(method: str, path: str) -> str:
    if path.startswith("/quote"):
        return "quote"
    if method == "POST" and path.startswith("/orders/"):
        return "order_place"
    return "other"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, get_state) -> None:
        super().__init__(app)
        self._get_state = get_state
        # (api_key, class) -> (window_second, count)
        self._windows: dict[tuple[str, str], tuple[int, int]] = {}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # The control plane and health check are our own surface, never throttled.
        if path == "/" or path.startswith("/_control"):
            return await call_next(request)

        state = self._get_state()
        if not state.config.rate_limit.enabled:
            return await call_next(request)

        cls = _endpoint_class(request.method, path)
        limit = _LIMITS.get(cls, 10)
        api_key = _api_key(request)
        now_s = int(time.time())
        key = (api_key, cls)
        window, count = self._windows.get(key, (now_s, 0))
        if window != now_s:
            window, count = now_s, 0
        count += 1
        self._windows[key] = (window, count)
        if count > limit:
            return error_response("Too many requests.", "NetworkException", 429)
        return await call_next(request)


def _api_key(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.startswith("token "):
        rest = header[len("token ") :]
        if ":" in rest:
            return rest.split(":", 1)[0]
    return "anonymous"
