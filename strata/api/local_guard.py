"""
@module api.local_guard
@purpose Stop web pages the operator happens to visit from driving the loopback API.
@owns Host and Origin checks, the UI origin allowlist, CORS setup, the per-launch token check
@does_not_own token storage (strata.api.api_token), route handlers
@key_exports install_local_guard, allowed_ui_origins

Binding to 127.0.0.1 keeps other machines out, but it does not keep out web
pages running in the operator's own browser. Without these checks any site
could (a) POST to an admin endpoint as a "simple" cross-site request, which
CORS never preflights, or (b) use DNS rebinding to read API responses. So the
API applies three layers, in this order:

1. Host: every request's Host header must be 127.0.0.1, localhost or [::1] on
   the port the API is serving. A rebound attacker domain fails here.
2. Origin: a request that carries an Origin must come from the workbench (the
   allowlist below). A request without one must not be marked cross-site or
   same-site by Sec-Fetch-Site. This applies to every method, preflights included.
3. Token: every POST/PUT/PATCH/DELETE must carry X-Strata-Token, a random value
   generated at API start. The workbench fetches it from GET /admin/session-token
   (answered only to the workbench's own origins; CORS keeps other pages from
   reading it). Local scripts read it from strata/runtime/api-token.

CORS is narrowed to the same allowlist; the wildcard is gone. None of this is
user authentication: anything that runs as the operator's account, or any page
served from an allowlisted origin, is trusted. Do not bind the API beyond loopback.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Iterable, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from strata.api.api_token import TOKEN_HEADER, current_token

UI_ORIGINS_ENV = "STRATA_UI_ORIGINS"
SESSION_TOKEN_PATH = "/admin/session-token"
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
_DUPLICATE = "\x00duplicate"  # a header sent more than once never matches anything

logger = logging.getLogger(__name__)

# The workbench's own origins. Vite's dev/demo server (5173), `vite preview`
# (4173), the Tauri dev frontend (scripts/desktop_dev_frontend.sh, 5174) and the
# Tauri desktop webview. Add more with STRATA_UI_ORIGINS (comma-separated).
DEFAULT_UI_ORIGINS: Tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


def _default_port() -> int:
    try:
        return int(os.environ.get("STRATA_API_PORT", "8000"))
    except ValueError:
        return 8000


def _own_origins(port: int) -> List[str]:
    """The API's own origins, for when it serves the built UI itself."""
    suffix = "" if port == 80 else f":{port}"
    return [f"http://{host}{suffix}" for host in ("127.0.0.1", "localhost", "[::1]")]


def _valid_origin(value: str) -> bool:
    if not value or value == "null" or "*" in value or value.endswith("/"):
        return False
    scheme, sep, rest = value.partition("://")
    return bool(sep) and scheme in {"http", "https", "tauri"} and bool(rest) and "/" not in rest and "@" not in rest


def allowed_ui_origins(port: Optional[int] = None, extra: Optional[Iterable[str]] = None) -> List[str]:
    """Exact origins allowed to call the API. Never contains '*' or 'null'."""
    origins = list(DEFAULT_UI_ORIGINS) + _own_origins(port if port is not None else _default_port())
    configured = list(extra) if extra is not None else str(os.environ.get(UI_ORIGINS_ENV) or "").split(",")
    for item in configured:
        candidate = str(item or "").strip().lower()
        if not candidate:
            continue
        if _valid_origin(candidate):
            origins.append(candidate)
        else:
            # Ignore (do not widen to) anything that is not a plain origin.
            logger.warning("Ignoring invalid %s entry: %r", UI_ORIGINS_ENV, item)
    seen = set()
    return [o for o in origins if not (o in seen or seen.add(o))]


def _header(scope, name: bytes) -> Optional[str]:
    values = [v for k, v in scope.get("headers") or [] if k == name]
    if len(values) != 1:
        return None if not values else _DUPLICATE
    return values[0].decode("latin-1")


def _parse_host(value: str) -> Optional[Tuple[str, Optional[int]]]:
    """Exact-form Host parsing: hostname[:port]. Anything a URL parser would reinterpret is refused."""
    if not value or any(c in value for c in "@/\\?# \t"):
        return None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        hostname, rest = value[: end + 1], value[end + 1 :]
    else:
        hostname, colon, port_text = value.partition(":")
        rest = f":{port_text}" if colon else ""
    if rest == "":
        return hostname.lower(), None
    if not rest.startswith(":") or not rest[1:].isdigit():
        return None
    return hostname.lower(), int(rest[1:])


def _serving_port(scope) -> int:
    server = scope.get("server")
    if server and len(server) >= 2 and server[1]:
        return int(server[1])
    return _default_port()


async def _send_json(send, status: int, detail: str, code: str) -> None:
    body = json.dumps({"detail": detail, "code": code}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class LocalRequestGuard:
    """Outermost layer: Host and Origin checks for every HTTP request. Rejections carry no CORS headers."""

    def __init__(self, app, extra_origins: Optional[Iterable[str]] = None):
        self.app = app
        self._extra = list(extra_origins) if extra_origins is not None else None
        self._origins_by_port: dict = {}

    def _origins(self, port: int) -> frozenset:
        cached = self._origins_by_port.get(port)
        if cached is None:
            cached = frozenset(allowed_ui_origins(port, self._extra))
            self._origins_by_port[port] = cached
        return cached

    async def __call__(self, scope, receive, send):
        # The API has no websocket routes; only HTTP needs guarding.
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        port = _serving_port(scope)
        parsed = _parse_host(_header(scope, b"host") or "")
        if parsed is None or parsed[0] not in LOOPBACK_HOSTNAMES or (parsed[1] or 80) != port:
            return await _send_json(send, 403, "Host not allowed: the Strata API answers only on loopback.", "strata_host_rejected")

        origin = _header(scope, b"origin")
        if origin is not None:
            if origin.lower() not in self._origins(port):
                return await _send_json(send, 403, "Origin not allowed.", "strata_origin_rejected")
        else:
            fetch_site = (_header(scope, b"sec-fetch-site") or "").lower()
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                return await _send_json(send, 403, "Cross-site request without an allowed Origin.", "strata_origin_rejected")
        return await self.app(scope, receive, send)


class ApiTokenGuard:
    """Inside CORS, so an allowed origin can read the 401 and fetch a fresh token."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method", "").upper() in STATE_CHANGING_METHODS:
            supplied = _header(scope, TOKEN_HEADER.lower().encode()) or ""
            if not hmac.compare_digest(supplied.encode("utf-8", "replace"), current_token().encode()):
                return await _send_json(
                    send,
                    401,
                    f"Missing or stale {TOKEN_HEADER}. The workbench fetches it from {SESSION_TOKEN_PATH}; "
                    "local scripts read strata/runtime/api-token.",
                    "strata_token_required",
                )
        return await self.app(scope, receive, send)


def install_local_guard(app: FastAPI, extra_origins: Optional[Iterable[str]] = None) -> None:
    """Install the guard layers and the token bootstrap route. Call once, before serving."""
    port = _default_port()
    origins = allowed_ui_origins(port, extra_origins)
    # add_middleware wraps outward: the last one added runs first.
    app.add_middleware(ApiTokenGuard)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", TOKEN_HEADER],
        allow_credentials=False,
    )
    app.add_middleware(LocalRequestGuard, extra_origins=extra_origins)

    @app.get(SESSION_TOKEN_PATH, include_in_schema=False)
    async def session_token(request: Request):
        # LocalRequestGuard has already checked Host and any Origin. Additionally
        # require positive evidence that the caller is the workbench page: an
        # allowed Origin (cross-origin fetch) or Sec-Fetch-Site: same-origin.
        has_origin = request.headers.get("origin") is not None
        same_origin = request.headers.get("sec-fetch-site", "").lower() == "same-origin"
        if not (has_origin or same_origin):
            return JSONResponse(
                {"detail": "Only the workbench page can fetch the session token; local scripts read strata/runtime/api-token.", "code": "strata_origin_rejected"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            {"token": current_token(), "header": TOKEN_HEADER},
            headers={"Cache-Control": "no-store"},
        )
