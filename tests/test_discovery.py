"""Machine-readable discovery: /.well-known/mcp.json, /.well-known/agent.json,
/about, and a helpful GET /mcp/ — so Tender stops getting probed wrong
(dead discovery paths, empty args, or treated as a paid seller expecting a
402). Also covers the duplicate-operation-id warning fix for /healthz."""
import warnings

import pytest
from fastapi.testclient import TestClient

from preflight.app import app

client = TestClient(app)

REAL_TOOL_NAMES = {"preflight_run", "get_report", "compare_services"}


def _assert_discovery_shape(doc: dict) -> None:
    assert doc["name"] == "Tender"
    assert doc["role"] == "buyer"
    assert doc["pricing"]["model"] == "free"
    assert doc["pricing"]["amount_usdt"] == 0
    assert "402" in doc["pricing"]["note"]  # explicitly says it doesn't issue one
    assert "free" in doc["summary"].lower()
    assert "buyer" in doc["summary"].lower() or "pays" in doc["summary"].lower()
    assert "does not sell" in doc["summary"] or "not a paid" in doc["pricing"]["note"]

    proto = doc["protocol"]
    assert proto["type"] == "mcp"
    assert proto["method"] == "POST"
    assert proto["endpoint"].endswith("/mcp/")
    assert proto["framing"] == "JSON-RPC 2.0"
    assert proto["example"]["method"] == "tools/list"

    tool_names = {t["name"] for t in doc["tools"]}
    assert tool_names == REAL_TOOL_NAMES
    for t in doc["tools"]:
        assert "description" in t and t["description"]
        assert "inputSchema" in t and t["inputSchema"]["type"] == "object"
    # compare_services' schema specifically must match the real registered tool
    compare = next(t for t in doc["tools"] if t["name"] == "compare_services")
    assert compare["inputSchema"]["required"] == ["targets"]
    assert "targets" in compare["inputSchema"]["properties"]


def test_well_known_mcp_json():
    resp = client.get("/.well-known/mcp.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    _assert_discovery_shape(resp.json())


def test_well_known_agent_json():
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    _assert_discovery_shape(resp.json())


def test_about_endpoint():
    resp = client.get("/about")
    assert resp.status_code == 200
    _assert_discovery_shape(resp.json())


def test_discovery_tool_schemas_match_real_mcp_tools_list():
    """The discovery doc's tool schemas must be the live ones, not a
    hand-copied snapshot that can drift."""
    import asyncio
    from preflight.app import mcp

    doc = client.get("/about").json()
    doc_by_name = {t["name"]: t for t in doc["tools"]}

    real_tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in real_tools} == REAL_TOOL_NAMES
    for t in real_tools:
        assert doc_by_name[t.name]["description"] == t.description
        assert doc_by_name[t.name]["inputSchema"] == t.parameters


def test_get_mcp_returns_helpful_405_not_bare():
    resp = client.get("/mcp/")
    assert resp.status_code == 405
    body = resp.json()
    assert "JSON-RPC" in body["message"]
    assert "POST" in body["message"]
    assert body["example"].startswith("curl -X POST")
    assert "tools/list" in body["example"]
    assert body["discovery"].endswith("/.well-known/mcp.json")


def test_post_mcp_still_works_unchanged():
    """The new GET /mcp/ route must not shadow POST — the real MCP transport
    still answers tools/list exactly as before. Needs the lifespan-managed
    client (FastMCP's session manager only initializes its task group on
    ASGI startup) — the bare module-level client skips that on purpose for
    the other tests here, which never touch the MCP transport itself."""
    with TestClient(app) as lifespan_client:
        resp = lifespan_client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 200
    data = resp.json()
    names = {t["name"] for t in data["result"]["tools"]}
    assert names == REAL_TOOL_NAMES


def test_post_mcp_with_no_accept_header_still_succeeds():
    """FastMCP's underlying transport 406s a POST with no Accept header at
    all (Client must accept application/json) — the shim ahead of the /mcp
    mount must widen it before the transport ever sees the request, exactly
    like the working free ASP (ScoutGate) tolerates the same omission."""
    with TestClient(app) as lifespan_client:
        resp = lifespan_client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            # deliberately no Accept header — httpx/TestClient won't add one
            headers={"accept": ""},
        )
    assert resp.status_code == 200
    data = resp.json()
    names = {t["name"] for t in data["result"]["tools"]}
    assert names == REAL_TOOL_NAMES


def test_post_mcp_with_json_only_accept_header_still_works():
    """Accept: application/json (no text/event-stream) must also succeed —
    Tender's transport runs in JSON-only mode (json_response=True), which
    only ever required application/json in the first place; the shim must
    not interfere with an Accept header that already satisfies the check."""
    with TestClient(app) as lifespan_client:
        resp = lifespan_client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json"},
        )
    assert resp.status_code == 200
    data = resp.json()
    names = {t["name"] for t in data["result"]["tools"]}
    assert names == REAL_TOOL_NAMES


def test_healthz_get_and_head_both_work():
    get_resp = client.get("/healthz")
    assert get_resp.status_code == 200
    assert get_resp.json()["ok"] is True

    head_resp = client.head("/healthz")
    assert head_resp.status_code == 200
    assert head_resp.content == b""


def test_openapi_build_has_no_duplicate_operation_id_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.openapi_schema = None  # force a rebuild
        app.openapi()
    duplicate_warnings = [w for w in caught if "Duplicate Operation ID" in str(w.message)]
    assert duplicate_warnings == [], [str(w.message) for w in duplicate_warnings]
