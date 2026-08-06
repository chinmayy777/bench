"""_MCPHandshakeExemption: MCP's own handshake/discovery methods (initialize,
notifications/initialized, tools/list, ping) must succeed unpaywalled, or a
client can never get far enough to discover — let alone pay for — a real
tool. Only tools/call stays behind the (currently zero-amount) x402 gate.

Before this, the whole /mcp mount 402'd uniformly, including `initialize`
itself, so a real MCP-transport client died on the handshake before ever
reaching a paid call ("endpoint_unreachable: initialize returned HTTP 402").
test_x402_seller.py::TestMcpEndpointHandshake covers the paid tools/call
path (unpaid -> 402, signed replay -> 200) and the still-gated bare-GET
regression; this file covers the newly-free handshake/discovery surface.
"""
from __future__ import annotations

import httpx


class TestHandshakeIsFree:
    """Drives the real Tender app end to end over HTTP."""

    def test_initialize_succeeds_with_no_payment(self, server_factory):
        from preflight.app import app

        with server_factory(app, 8995):
            resp = httpx.post(
                "http://127.0.0.1:8995/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "1"}}},
            )
        assert resp.status_code == 200
        assert "payment-required" not in resp.headers
        body = resp.json()
        assert body["result"]["serverInfo"]["name"] == "Tender"

    def test_tools_list_succeeds_with_no_payment_and_lists_real_tools(self, server_factory):
        """The whole point of freeing tools/list: a buyer must be able to
        discover compare_services (and its real inputSchema) before it can
        ever call it — it can't discover a tool it was charged to see."""
        from preflight.app import app

        with server_factory(app, 8996):
            resp = httpx.post(
                "http://127.0.0.1:8996/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        assert resp.status_code == 200
        assert "payment-required" not in resp.headers
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        assert {"preflight_run", "get_report", "compare_services"} <= tools.keys()
        # Real schema, not a stub — the exact thing a buyer needs to call it.
        assert "targets" in tools["compare_services"]["inputSchema"]["properties"]

    def test_notifications_initialized_succeeds_with_no_payment(self, server_factory):
        from preflight.app import app

        with server_factory(app, 8997):
            resp = httpx.post(
                "http://127.0.0.1:8997/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
        # A JSON-RPC notification carries no id and gets no result body back
        # from the MCP transport (202 Accepted, empty body) — the only thing
        # under test here is that it wasn't paywalled.
        assert resp.status_code == 202
        assert "payment-required" not in resp.headers

    def test_ping_succeeds_with_no_payment(self, server_factory):
        from preflight.app import app

        with server_factory(app, 9001):
            resp = httpx.post(
                "http://127.0.0.1:9001/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
            )
        assert resp.status_code == 200
        assert "payment-required" not in resp.headers
        assert resp.json()["result"] == {}

    def test_tools_call_is_still_paywalled(self, server_factory):
        """The one method that must NOT be exempted — a free handshake isn't
        a free service. Belt-and-suspenders alongside
        test_x402_seller.py::test_unpaid_call_gets_a_correct_zero_amount_402."""
        from preflight.app import app

        with server_factory(app, 9002):
            resp = httpx.post(
                "http://127.0.0.1:9002/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "get_report", "arguments": {"report_id": "nope"}}},
            )
        assert resp.status_code == 402
        assert resp.headers.get("payment-required")

    def test_unrecognized_method_falls_through_to_paywall(self, server_factory):
        """Unknown methods default to paywalled, not free — the allowlist is
        a strict opt-in, never an opt-out for tools/call in disguise."""
        from preflight.app import app

        with server_factory(app, 9003):
            resp = httpx.post(
                "http://127.0.0.1:9003/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
            )
        assert resp.status_code == 402
        assert resp.headers.get("payment-required")

    def test_bare_get_is_unaffected_still_402(self, server_factory):
        """GET carries no JSON-RPC method to exempt, and the marketplace's
        listing validator still expects a 402 on it (see
        test_x402_seller.py::test_bare_get_also_gets_the_402_challenge) —
        confirms _MCPHandshakeExemption really does ignore non-POST traffic
        rather than accidentally widening the free surface."""
        from preflight.app import app

        with server_factory(app, 9004):
            resp = httpx.get("http://127.0.0.1:9004/mcp/", timeout=15.0)
        assert resp.status_code == 402
        assert resp.headers.get("payment-required")
