"""_MCPContentTypeShim: a real paid replay to /mcp/ was observed hitting a
bare-text 400 "Invalid Content-Type header" from mcp.server.transport_security
-- a SEPARATE, earlier check than the one _MCPAcceptHeaderShim already works
around, with its own case-insensitive-prefix + missing-header failure mode.

These tests drive the real deployed app end to end (unpaid -> 402 -> signed
replay), varying only Content-Type, to prove the fix without touching
anything else in the request.
"""
from __future__ import annotations

import json as jsonlib

import httpx
from x402.http.utils import decode_payment_required_header, encode_payment_signature_header

from preflight.app import app
from test_x402_seller import _sign

URL_TMPL = "http://127.0.0.1:{port}/mcp/"
BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _get_challenge(port: int, unpaid_content_type: str) -> dict:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": unpaid_content_type}
    resp = httpx.post(URL_TMPL.format(port=port), timeout=15.0, headers=headers,
                      content=jsonlib.dumps(BODY))
    assert resp.status_code == 402, f"expected 402 fetching the challenge, got {resp.status_code}"
    return decode_payment_required_header(resp.headers["payment-required"]).accepts[0]


def _paid_call(port: int, content_type: str | None) -> httpx.Response:
    """content_type=None means send the request with NO Content-Type header
    at all -- using httpx's `content=` (not `json=`, which auto-sets one)."""
    req = _get_challenge(port, unpaid_content_type="application/json")
    payload = _sign(req)
    headers = {"Accept": "application/json, text/event-stream",
              "PAYMENT-SIGNATURE": encode_payment_signature_header(payload)}
    if content_type is not None:
        headers["Content-Type"] = content_type
    return httpx.post(URL_TMPL.format(port=port), timeout=15.0, headers=headers,
                      content=jsonlib.dumps(BODY))


class TestUnpaidChallengeUnaffectedByContentType:
    """The x402 payment gate runs before the MCP mount and never inspects
    Content-Type -- confirms the shim doesn't change the unpaid 402 path."""

    def test_unpaid_call_still_gets_402_with_a_bad_content_type(self, server_factory):
        with server_factory(app, 8995):
            req = _get_challenge(8995, unpaid_content_type="text/json")
        assert req.amount == "0"


class TestPreviouslyRejectedContentTypesNowSucceed:
    def test_missing_content_type_now_returns_200(self, server_factory):
        with server_factory(app, 8996):
            resp = _paid_call(8996, content_type=None)
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    def test_text_json_now_returns_200(self, server_factory):
        """The exact real-world failure: bare 400 'Invalid Content-Type
        header' from mcp.server.transport_security."""
        with server_factory(app, 8997):
            resp = _paid_call(8997, content_type="text/json")
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    def test_mixed_case_content_type_now_returns_200(self, server_factory):
        """Passes transport_security's lenient check but used to fail
        streamable_http's case-sensitive one (415)."""
        with server_factory(app, 8998):
            resp = _paid_call(8998, content_type="Application/JSON")
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    def test_uppercase_with_charset_now_returns_200(self, server_factory):
        with server_factory(app, 8999):
            resp = _paid_call(8999, content_type="APPLICATION/JSON; charset=utf-8")
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]


class TestAlreadyWorkingContentTypesUnaffected:
    """Real clients already sending a conforming Content-Type must see no
    change at all -- the shim only touches what would otherwise fail."""

    def test_plain_application_json_unaffected(self, server_factory):
        with server_factory(app, 9000):
            resp = _paid_call(9000, content_type="application/json")
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    def test_application_json_with_charset_unaffected(self, server_factory):
        with server_factory(app, 9001):
            resp = _paid_call(9001, content_type="application/json; charset=utf-8")
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]

    def test_application_json_with_extra_params_unaffected(self, server_factory):
        with server_factory(app, 9002):
            resp = _paid_call(9002, content_type="application/json; charset=utf-8; boundary=x")
        assert resp.status_code == 200
        assert "tools" in resp.json()["result"]
