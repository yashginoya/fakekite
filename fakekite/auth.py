"""Auth: parse the REST ``Authorization`` header and the WS query params.

The terminal sends ``Authorization: token api_key:access_token`` on every REST
call, and ``api_key`` + ``access_token`` as WS query params. With the default
``auth.mode: accept_any`` any well-formed credentials are accepted; with
``fixed`` only the configured pair is. No interactive/browser login exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .config import AuthConfig
from .rest.envelopes import KiteError


@dataclass(frozen=True)
class Credentials:
    api_key: str
    access_token: str


def parse_rest_auth(header: str | None) -> Credentials:
    """Parse ``token api_key:access_token``; raise ``TokenException`` if malformed."""
    if not header:
        raise KiteError("Missing `Authorization` header.", "TokenException", 403)
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0] != "token":
        raise KiteError(
            "Invalid `Authorization` header. Expected `token api_key:access_token`.",
            "TokenException",
            403,
        )
    pair = parts[1]
    if ":" not in pair:
        raise KiteError("Invalid `Authorization` header.", "TokenException", 403)
    api_key, access_token = pair.split(":", 1)
    if not api_key or not access_token:
        raise KiteError("Invalid `Authorization` header.", "TokenException", 403)
    return Credentials(api_key=api_key, access_token=access_token)


def _check_fixed(cfg: AuthConfig, creds: Credentials) -> None:
    if cfg.mode == "fixed" and (
        creds.api_key != cfg.api_key or creds.access_token != cfg.access_token
    ):
        raise KiteError("Invalid `api_key` or `access_token`.", "TokenException", 403)


async def authenticate(request: Request) -> Credentials:
    """FastAPI dependency: validate REST auth and return the credentials."""
    cfg: AuthConfig = request.app.state.fk.config.auth
    creds = parse_rest_auth(request.headers.get("authorization"))
    _check_fixed(cfg, creds)
    return creds


def check_ws_auth(cfg: AuthConfig, api_key: str | None, access_token: str | None) -> bool:
    """Validate WS handshake query params. Returns True if the connection is allowed."""
    if not api_key or not access_token:
        return False
    if cfg.mode == "fixed":
        return api_key == cfg.api_key and access_token == cfg.access_token
    return True
