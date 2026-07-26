"""Machine-readable discovery: /.well-known/mcp.json, /.well-known/agent.json,
/about, and a helpful GET /mcp/ — so Tender stops getting probed wrong
(dead discovery paths, empty args, or treated as a paid seller expecting a
402). Also covers the duplicate-operation-id warning fix for /healthz."""
import warnings

import pytest
from fastapi.testclient import TestClient
from x402.http.utils import decode_payment_required_header, encode_payment_signature_header

from preflight.app import app
from test_x402_seller import _sign

client = TestClient(app)


def _paid_signature_header(unpaid_resp):
    """Sign a valid zero-value authorization against the 402 this app just
    issued, so POST tests can drive the real gated /mcp/ past it."""
    req = decode_payment_required_header(unpaid_resp.headers["payment-required"]).accepts[0]
    return encode_payment_signature_header(_sign(req))

REAL_TOOL_NAMES = {"preflight_run", "get_report", "compare_services"}


def _assert_discovery_shape(doc: dict) -> None:
    assert doc["name"] == "Tender"
    assert doc["role"] == "buyer"
    assert doc["pricing"]["model"] == "free"
    assert doc["pricing"]["amount_usdt"] == 0
    assert "402" in doc["pricing"]["note"]  # explains its 402 always quotes amount 0
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


def test_get_mcp_returns_402_not_bare():
    """The x402 validator probes with a bare GET and rejects any ASP that
    doesn't 402 on it (TRACE, the working reference, gates GET too) — so an
    unpaid GET must get the same challenge as an unpaid POST, not a 405."""
    resp = client.get("/mcp/")
    assert resp.status_code == 402
    header = resp.headers.get("payment-required")
    assert header
    req = decode_payment_required_header(header).accepts[0]
    assert req.amount == "0"
    assert req.scheme == "exact"
    assert req.network == "eip155:196"


def test_paid_get_acknowledges_settlement_with_a_helpful_hint():
    """A GET carries no JSON-RPC body/method, so there's no tool call to
    fulfill once payment clears — but it must still be a 200 (the signed-
    replay flow applies to GET too), with the same practical guidance the
    old bare-405 hint used to give."""
    unpaid = client.get("/mcp/")
    assert unpaid.status_code == 402
    signature = _paid_signature_header(unpaid)
    resp = client.get("/mcp/", headers={"PAYMENT-SIGNATURE": signature})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "POST" in body["message"]
    assert body["example"].startswith("curl -X POST")
    assert "tools/list" in body["example"]
    assert body["discovery"].endswith("/.well-known/mcp.json")


def test_post_mcp_still_works_unchanged():
    """The new GET /mcp/ route must not shadow POST — the real MCP transport
    still answers tools/list exactly as before, once the (now mandatory,
    zero-amount) x402 handshake is satisfied. Needs the lifespan-managed
    client (FastMCP's session manager only initializes its task group on
    ASGI startup) — the bare module-level client skips that on purpose for
    the other tests here, which never touch the MCP transport itself."""
    headers = {"accept": "application/json, text/event-stream"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with TestClient(app) as lifespan_client:
        unpaid = lifespan_client.post("/mcp/", json=body, headers=headers)
        assert unpaid.status_code == 402
        headers = {**headers, "PAYMENT-SIGNATURE": _paid_signature_header(unpaid)}
        resp = lifespan_client.post("/mcp/", json=body, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    names = {t["name"] for t in data["result"]["tools"]}
    assert names == REAL_TOOL_NAMES


def test_post_mcp_with_no_accept_header_still_succeeds():
    """FastMCP's underlying transport 406s a POST with no Accept header at
    all (Client must accept application/json) — the shim ahead of the /mcp
    mount must widen it before the transport ever sees the request, exactly
    like the working free ASP (ScoutGate) tolerates the same omission. The
    x402 gate sits in front of that shim and must not interfere with it."""
    headers = {"accept": ""}  # deliberately no Accept header
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with TestClient(app) as lifespan_client:
        unpaid = lifespan_client.post("/mcp/", json=body, headers=headers)
        assert unpaid.status_code == 402
        headers = {**headers, "PAYMENT-SIGNATURE": _paid_signature_header(unpaid)}
        resp = lifespan_client.post("/mcp/", json=body, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    names = {t["name"] for t in data["result"]["tools"]}
    assert names == REAL_TOOL_NAMES


def test_post_mcp_with_json_only_accept_header_still_works():
    """Accept: application/json (no text/event-stream) must also succeed —
    Tender's transport runs in JSON-only mode (json_response=True), which
    only ever required application/json in the first place; the shim must
    not interfere with an Accept header that already satisfies the check,
    and neither must the x402 gate in front of it."""
    headers = {"accept": "application/json"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with TestClient(app) as lifespan_client:
        unpaid = lifespan_client.post("/mcp/", json=body, headers=headers)
        assert unpaid.status_code == 402
        headers = {**headers, "PAYMENT-SIGNATURE": _paid_signature_header(unpaid)}
        resp = lifespan_client.post("/mcp/", json=body, headers=headers)
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
