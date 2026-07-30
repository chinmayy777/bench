"""_MCPEmptyBodyShim: a real paid replay to /mcp/ was observed hitting a bare
400 "Parse error" / "Validation error" from mcp.server.streamable_http's
json.loads()/JSONRPCMessage.model_validate() when the POST body was empty or
not a valid JSON-RPC 2.0 message -- AFTER PaymentMiddlewareASGI had already
verified the (zero-cost) payment. The fix returns 200 with a deliverable
(the same discovery/usage payload served at /.well-known/mcp.json) instead
of failing the replay.

These tests drive the real deployed app end to end (unpaid -> 402 -> signed
replay), varying only the body, to prove the fix without touching anything
else in the request.
"""
from __future__ import annotations

import json as jsonlib

import httpx
from x402.http.utils import decode_payment_required_header, encode_payment_signature_header

from preflight.app import app
from test_x402_seller import _sign

URL_TMPL = "http://127.0.0.1:{port}/mcp/"
BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _get_challenge(port: int) -> dict:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    resp = httpx.post(URL_TMPL.format(port=port), timeout=15.0, headers=headers,
                      content=jsonlib.dumps(BODY))
    assert resp.status_code == 402, f"expected 402 fetching the challenge, got {resp.status_code}"
    return decode_payment_required_header(resp.headers["payment-required"]).accepts[0]


def _paid_call(port: int, content: bytes) -> httpx.Response:
    req = _get_challenge(port)
    payload = _sign(req)
    headers = {"Accept": "application/json, text/event-stream",
              "Content-Type": "application/json",
              "PAYMENT-SIGNATURE": encode_payment_signature_header(payload)}
    return httpx.post(URL_TMPL.format(port=port), timeout=15.0, headers=headers, content=content)


class TestUnpaidChallengeUnaffectedByBody:
    """The x402 payment gate runs before the MCP mount and never inspects
    the body -- confirms the shim doesn't change the unpaid 402 path."""

    def test_unpaid_call_with_empty_body_still_gets_402(self, server_factory):
        with server_factory(app, 9010):
            headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
            resp = httpx.post(URL_TMPL.format(port=9010), timeout=15.0, headers=headers, content=b"")
        assert resp.status_code == 402


class TestPreviouslyRejectedBodiesNowSucceed:
    def test_empty_body_after_payment_returns_200_deliverable(self, server_factory):
        """The exact real-world failure: a paid replay with an empty body
        used to hit a bare 400 'Parse error' from
        mcp.server.streamable_http instead of any deliverable."""
        with server_factory(app, 9011):
            resp = _paid_call(9011, content=b"")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert "tools" in payload
        assert payload["protocol"]["endpoint"].endswith("/mcp/")

    def test_non_jsonrpc_body_after_payment_returns_200_deliverable(self, server_factory):
        """Valid JSON, but not the JSON-RPC 2.0 shape -- used to fail
        JSONRPCMessage.model_validate() with a 400 'Validation error'."""
        with server_factory(app, 9012):
            resp = _paid_call(9012, content=jsonlib.dumps({"hello": "world"}).encode())
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert "tools" in payload

    def test_non_json_body_after_payment_returns_200_deliverable(self, server_factory):
        """Not even JSON -- used to fail json.loads() with a 400 'Parse error'."""
        with server_factory(app, 9013):
            resp = _paid_call(9013, content=b"not json at all")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True


class TestWellFormedCallsUnaffected:
    """Real, well-formed JSON-RPC calls must see no change at all -- the
    shim only touches bodies that would otherwise fail to parse/validate."""

    def test_valid_tools_list_call_unaffected(self, server_factory):
        with server_factory(app, 9014):
            resp = _paid_call(9014, content=jsonlib.dumps(BODY).encode())
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    def test_call_to_unknown_method_still_a_normal_jsonrpc_error(self, server_factory):
        """A syntactically valid JSON-RPC call to a method that doesn't
        exist is MCP's job to reject, not this shim's -- it already comes
        back as a 200 with a JSON-RPC-level error, not an HTTP 400."""
        unknown = {"jsonrpc": "2.0", "id": 1, "method": "not/a/real/method"}
        with server_factory(app, 9015):
            resp = _paid_call(9015, content=jsonlib.dumps(unknown).encode())
        assert resp.status_code == 200
        assert "error" in resp.json()
