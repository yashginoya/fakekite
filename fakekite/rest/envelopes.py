"""Success / error envelope helpers and the ``KiteError`` exception.

Every JSON response uses one of two shapes (see KITE_SPEC.md §1):

    success: {"status": "success", "data": ...}
    error:   {"status": "error", "message": "...", "error_type": "..."}

Raising :class:`KiteError` anywhere in a request renders the error envelope with
the right HTTP status. The ``error_type -> status`` defaults come straight from
the spec table; a handler may override the code (some error types map to more
than one). The error-injection plumbing in Milestone 3 reuses ``KiteError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from ..state import AppState

# error_type -> default HTTP status (KITE_SPEC.md §1).
ERROR_TYPE_STATUS: dict[str, int] = {
    "TokenException": 403,
    "UserException": 403,
    "OrderException": 400,
    "InputException": 400,
    "MarginException": 400,
    "HoldingException": 400,
    "NetworkException": 503,
    "DataException": 500,
    "GeneralException": 500,
}


class KiteError(Exception):
    """An error to render as a Kite error envelope with a matching HTTP code."""

    def __init__(
        self,
        message: str,
        error_type: str = "GeneralException",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code or ERROR_TYPE_STATUS.get(error_type, 500)


@dataclass
class ErrorInjection:
    """A pending forced error for a named endpoint (set via the control plane)."""

    error_type: str
    message: str
    status_code: int | None = None
    count: int = 1  # remaining triggers; -1 means until cleared


def maybe_inject(state: AppState, endpoint: str) -> None:
    """Raise the configured ``KiteError`` for ``endpoint`` if one is pending.

    Endpoints register a stable key (e.g. ``"orders.place"``); the control plane
    (Milestone 4) populates ``state.error_injections`` and this consumes them.
    """
    inj = state.error_injections.get(endpoint)
    if inj is None or inj.count == 0:
        return
    if inj.count > 0:
        inj.count -= 1
        if inj.count == 0:
            state.error_injections.pop(endpoint, None)
    raise KiteError(inj.message, inj.error_type, inj.status_code)


def success(data: Any) -> dict[str, Any]:
    return {"status": "success", "data": data}


def error_payload(message: str, error_type: str) -> dict[str, str]:
    return {"status": "error", "message": message, "error_type": error_type}


def error_response(message: str, error_type: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(message, error_type),
        headers={"X-Kite-Version": "3"},
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(KiteError)
    async def _handle_kite_error(_request: Request, exc: KiteError) -> JSONResponse:
        return error_response(exc.message, exc.error_type, exc.status_code)
