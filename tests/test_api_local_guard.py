"""The loopback API must not be drivable by web pages the operator visits.

Covers strata/api/local_guard.py: Host checks (DNS rebinding), Origin checks
(cross-site "simple" requests that CORS never preflights), the CORS allowlist,
and the per-launch write token. Requests are rejected before any route runs;
the storage dependency is replaced with a tripwire so a regression fails the
test instead of touching the database.
"""

import asyncio
import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from strata.api import api_token
from strata.api import main as api_main
from strata.api.local_guard import allowed_ui_origins, install_local_guard

API = "http://127.0.0.1:8000"
UI_ORIGIN = "http://localhost:5173"
EVIL_ORIGIN = "https://evil.example"
TOKEN = api_token.current_token()


class StorageTripwire(Exception):
    pass


@pytest.fixture
def client():
    calls = []

    def tripwire():
        calls.append(1)
        raise StorageTripwire("a guarded request reached a route handler")

    api_main.app.dependency_overrides[api_main.get_storage] = tripwire
    try:
        test_client = TestClient(api_main.app, base_url=API, raise_server_exceptions=False)
        test_client.tripwire_calls = calls
        yield test_client
    finally:
        api_main.app.dependency_overrides.pop(api_main.get_storage, None)


def _no_cors_grant(response):
    return "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_cross_site_simple_post_to_reset_is_rejected(client):
    # What a malicious page can send without a preflight: a text/plain POST.
    response = client.post(
        "/admin/reset",
        content="x",
        headers={"Origin": EVIL_ORIGIN, "Content-Type": "text/plain", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "strata_origin_rejected"
    assert _no_cors_grant(response)
    # Even with a stolen token, a foreign Origin is refused.
    response = client.post("/admin/reset", headers={"Origin": EVIL_ORIGIN, "X-Strata-Token": TOKEN})
    assert response.status_code == 403
    # A cross-site request whose Origin was stripped (no-cors, referrer policy) is refused too.
    response = client.post("/admin/reset", headers={"Sec-Fetch-Site": "cross-site", "X-Strata-Token": TOKEN})
    assert response.status_code == 403
    # "null" (sandboxed iframes, file:// pages) is not an allowed origin.
    response = client.post("/admin/reset", headers={"Origin": "null", "Content-Type": "text/plain"})
    assert response.status_code == 403
    assert client.tripwire_calls == []


def test_writes_without_the_token_are_rejected_even_from_the_ui_origin(client):
    for headers in ({}, {"Origin": UI_ORIGIN}, {"Origin": UI_ORIGIN, "X-Strata-Token": "stale-token"}):
        response = client.post("/admin/reset", headers=headers)
        assert response.status_code == 401, headers
        assert response.json()["code"] == "strata_token_required"
    # The UI origin can read the 401 (so it can fetch a fresh token); it is never a wildcard grant.
    assert response.headers["access-control-allow-origin"] == UI_ORIGIN
    assert client.tripwire_calls == []


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "rebind.attacker.example:8000",  # DNS rebinding keeps the attacker's hostname
        "127.0.0.1.nip.io:8000",
        "127.0.0.1:8000@evil.example",
        "localhost:9999",  # right host, wrong port
        "127.0.0.1",  # implies port 80
        "",
    ],
)
def test_forged_host_is_rejected(client, host):
    for method in ("GET", "POST"):
        response = client.request(method, "/admin/test", headers={"Host": host, "X-Strata-Token": TOKEN})
        assert response.status_code == 403, (method, host)
        assert response.json()["code"] == "strata_host_rejected"
    response = client.post("/admin/reset", headers={"Host": host, "Origin": UI_ORIGIN, "X-Strata-Token": TOKEN})
    assert response.status_code == 403
    assert client.tripwire_calls == []


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000"])
def test_loopback_hosts_are_accepted(client, host):
    response = client.get("/admin/test", headers={"Host": host})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cross_origin_model_endpoint_swap_is_rejected(client):
    attacker_registry = {
        "trainer": {
            "endpoints": [
                {
                    "provider": "openrouter",
                    "model": "x",
                    "transport": "cloud",
                    "endpoint_url": "https://attacker.example/v1/chat/completions",
                    "api_key_env": "OPENROUTER_API_KEY",
                }
            ]
        }
    }
    # The preflight a JSON POST needs is refused, with no CORS grant...
    preflight = client.options(
        "/admin/registry",
        headers={
            "Origin": EVIL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 403
    assert _no_cors_grant(preflight)
    # ...and so is the POST itself, as JSON or as a no-preflight text/plain body.
    for content_type in ("application/json", "text/plain"):
        response = client.post(
            "/admin/registry",
            json=attacker_registry if content_type == "application/json" else None,
            content=None if content_type == "application/json" else '{"trainer": {}}',
            headers={"Origin": EVIL_ORIGIN, "Content-Type": content_type},
        )
        assert response.status_code == 403
        assert _no_cors_grant(response)
    assert client.tripwire_calls == []
    registry = client.get("/admin/registry", headers={"Origin": UI_ORIGIN}).json()
    assert "attacker.example" not in str(registry)


def test_ui_origin_can_fetch_the_token_and_write(client):
    preflight = client.options(
        "/models/select",
        headers={
            "Origin": UI_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-strata-token",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == UI_ORIGIN

    response = client.get("/admin/session-token", headers={"Origin": UI_ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == UI_ORIGIN
    assert response.headers["cache-control"] == "no-store"
    token = response.json()["token"]
    assert token == TOKEN and len(token) >= 32

    previous = api_main._model.active_model
    try:
        response = client.post(
            "/models/select",
            json={"model": "guard-test-model"},
            headers={"Origin": UI_ORIGIN, "X-Strata-Token": token},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == UI_ORIGIN
        assert api_main._model.active_model == "guard-test-model"
    finally:
        api_main._model.active_model = previous


def test_session_token_is_not_handed_to_foreign_or_unidentified_callers(client):
    assert client.get("/admin/session-token", headers={"Origin": EVIL_ORIGIN}).status_code == 403
    # <script src> / <img src> from another site: no Origin, Sec-Fetch-Site cross-site.
    assert client.get("/admin/session-token", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # A bare client with no browser evidence reads the token file instead.
    assert client.get("/admin/session-token").status_code == 403
    # The workbench served by the API itself (same origin) may fetch it.
    assert client.get("/admin/session-token", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_local_script_without_origin_can_write_with_the_token(client):
    previous = api_main._model.active_model
    try:
        response = client.post("/models/select", json={"model": "script-model"}, headers={"X-Strata-Token": TOKEN})
        assert response.status_code == 200
    finally:
        api_main._model.active_model = previous


def test_reads_from_foreign_origins_get_no_cors_grant(client):
    response = client.get("/admin/registry", headers={"Origin": EVIL_ORIGIN})
    assert response.status_code == 403
    assert _no_cors_grant(response)


def test_guard_stops_requests_before_the_handler_runs():
    hits = []
    app = FastAPI()
    install_local_guard(app)

    @app.post("/danger")
    async def danger():
        hits.append(1)
        return {"ok": True}

    client = TestClient(app, base_url=API)
    assert client.post("/danger", content="x", headers={"Origin": EVIL_ORIGIN, "Content-Type": "text/plain"}).status_code == 403
    assert client.post("/danger", headers={"Host": "evil.example:8000", "X-Strata-Token": TOKEN}).status_code == 403
    assert client.post("/danger", headers={"Origin": UI_ORIGIN}).status_code == 401
    assert hits == []
    assert client.post("/danger", headers={"Origin": UI_ORIGIN, "X-Strata-Token": TOKEN}).json() == {"ok": True}
    assert hits == [1]


def test_origin_allowlist_never_widens():
    origins = allowed_ui_origins(8000, ["*", "null", "https://ok.example/path", "http://user@evil.example", " http://localhost:6006 "])
    assert "*" not in origins and "null" not in origins
    assert not any("evil" in o or "/path" in o for o in origins)
    assert "http://localhost:6006" in origins
    assert UI_ORIGIN in origins and "http://127.0.0.1:8000" in origins


def test_token_file_is_private_and_reads_safely(tmp_path):
    target = api_token.write_token_file(tmp_path / "runtime" / "api-token")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert api_token.read_token_file(target) == TOKEN

    # A symlink planted at the path is replaced, not written through.
    victim = tmp_path / "victim"
    victim.write_text("keep")
    link = tmp_path / "linked-token"
    link.symlink_to(victim)
    api_token.write_token_file(link)
    assert victim.read_text() == "keep"
    assert api_token.read_token_file(link) == TOKEN

    # Readers refuse symlinks and FIFOs (and do not hang on a FIFO).
    other = tmp_path / "other-link"
    other.symlink_to(target)
    assert api_token.read_token_file(other) is None
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert api_token.read_token_file(fifo) is None


def _guard_only_app():
    hits = []
    app = FastAPI()
    install_local_guard(app)

    @app.post("/danger")
    async def danger():
        hits.append(1)
        return {"ok": True}

    return app, hits


def _raw_status(app, method, path, headers):
    """Drive the ASGI app with an exact header list, duplicates included (HTTP clients merge them)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers],
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 50000),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_duplicate_host_origin_or_token_headers_are_refused():
    app, hits = _guard_only_app()
    host = ("Host", "127.0.0.1:8000")
    origin = ("Origin", UI_ORIGIN)
    token = ("X-Strata-Token", TOKEN)
    # Positive control: one of each is accepted.
    assert _raw_status(app, "POST", "/danger", [host, origin, token]) == 200
    assert hits == [1]
    # A header sent twice never matches, even when both copies are valid.
    assert _raw_status(app, "POST", "/danger", [host, host, origin, token]) == 403
    assert _raw_status(app, "POST", "/danger", [host, ("Host", "evil.example:8000"), origin, token]) == 403
    assert _raw_status(app, "POST", "/danger", [host, origin, origin, token]) == 403
    assert _raw_status(app, "POST", "/danger", [host, origin, ("Origin", EVIL_ORIGIN), token]) == 403
    assert _raw_status(app, "POST", "/danger", [host, origin, token, token]) == 401
    assert hits == [1]


def test_same_site_request_without_origin_is_refused(client):
    # A same-site page (another port on localhost) whose Origin was stripped.
    response = client.post("/admin/reset", headers={"Sec-Fetch-Site": "same-site", "X-Strata-Token": TOKEN})
    assert response.status_code == 403
    assert response.json()["code"] == "strata_origin_rejected"
    assert client.get("/admin/session-token", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.tripwire_calls == []


def test_ipv6_loopback_ui_origins_are_allowed(client):
    for origin in ("http://[::1]:5173", "http://[::1]:4173", "http://[::1]:5174"):
        assert origin in allowed_ui_origins(8000, [])
    preflight = client.options(
        "/models/select",
        headers={
            "Origin": "http://[::1]:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-strata-token",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://[::1]:5173"


def test_cors_follows_the_served_port(monkeypatch):
    # `uvicorn --port 8123` without STRATA_API_PORT: the API's own origin on 8123 gets a CORS grant.
    monkeypatch.delenv("STRATA_API_PORT", raising=False)
    app, _ = _guard_only_app()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    own = "http://127.0.0.1:8123"
    response = TestClient(app, base_url=own).get("/ping", headers={"Origin": own})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == own
    # The default port's own origin is not granted on 8123.
    response = TestClient(app, base_url=own).get("/ping", headers={"Origin": "http://127.0.0.1:8000"})
    assert response.status_code == 403
