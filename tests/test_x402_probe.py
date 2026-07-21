"""Version-agnostic x402 challenge handling — driven by real, hand-captured 402s.

Fixtures below are verbatim byte-for-byte captures (unpaid GET/POST, no
payment sent) from four live marketplace ASPs on 2026-07-21:

  BrandCanvas, PixelBrief, Newsliquid — header-only (PAYMENT-REQUIRED, base64
  v2 JSON), empty `{}` body, single "exact" scheme, network eip155:196.

  oklink (Onchain Data Explorer) — body-only v2 JSON (no PAYMENT-REQUIRED
  header at all), GET returns 405 and POST returns the 402, two schemes
  ("exact" + "aggr_deferred"), network eip155:196.

Plus one v1 fixture (built through the SDK, since v1 is the legacy shape our
own demo fixture already speaks) and several malformed variants.
"""
from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from preflight.x402_probe import (
    ChallengeParseError,
    classify_payability,
    extract_challenge_payload,
    fetch_with_verb_fallback,
    parse_challenge_payload,
    payment_header_name,
    settlement_header_name,
)

# --- Captured fixtures ------------------------------------------------------

BRANDCANVAS_HEADER = (
    "eyJ4NDAyVmVyc2lvbiI6MiwiZXJyb3IiOiJQYXltZW50IHJlcXVpcmVkIiwicmVzb3VyY2UiOnsi"
    "dXJsIjoiaHR0cHM6Ly9icmFuZGNhbnZhcy5vbnJlbmRlci5jb20vYnJhbmQvZXh0cmFjdCIsImRl"
    "c2NyaXB0aW9uIjoiQ29tcGxldGUgYnJhbmQga2l0IGV4dHJhY3RlZCBmcm9tIGFueSBsaXZlIFVS"
    "TCB2aWEgaGVhZGxlc3MgQ2hyb21pdW0g4oCUIGNvbG9ycywgZm9udHMsIGxvZ28sIHNwYWNpbmcs"
    "IGNvbXBvbmVudHMsIGFuZCBDU1MgZGVzaWduIHRva2Vucy4gU2VuZDoge1widXJsXCI6IFwiaHR0"
    "cHM6Ly9leGFtcGxlLmNvbVwifSAocmVxdWlyZWQpLiIsIm1pbWVUeXBlIjoiYXBwbGljYXRpb24v"
    "anNvbiJ9LCJhY2NlcHRzIjpbeyJzY2hlbWUiOiJleGFjdCIsIm5ldHdvcmsiOiJlaXAxNTU6MTk2"
    "IiwiYW1vdW50IjoiNTAwMDAwIiwiYXNzZXQiOiIweDc3OWRlZDBjOWUxMDIyMjI1ZjhlMDYzMGIz"
    "NWE5YjU0YmU3MTM3MzYiLCJwYXlUbyI6IjB4MzdmYWZhM2UzNmFhNWMwZDZlZjM1NjI3ZmQ2NmQ1"
    "OTkxYTZmZDRkMSIsIm1heFRpbWVvdXRTZWNvbmRzIjozMDAsImV4dHJhIjp7Im5hbWUiOiJVU0Ti"
    "gq4wIiwidmVyc2lvbiI6IjEifX1dfQ=="
)

PIXELBRIEF_HEADER = (
    "eyJ4NDAyVmVyc2lvbiI6MiwiZXJyb3IiOiJQYXltZW50IHJlcXVpcmVkIiwicmVzb3VyY2UiOnsi"
    "dXJsIjoiaHR0cDovL3d3dy5waXhlbGJyaWVmLnRlY2gvdjEvYnJhbmQta2l0IiwiZGVzY3JpcHRp"
    "b24iOiJQaXhlbEJyaWVmIGZ1bGwgYnJhbmQga2l0OiBsb2dvIFNWRywgcGFsZXR0ZSwgdHlwZSwg"
    "c29jaWFsIHBvc3RzLCB0aHVtYm5haWwgYnJpZWYiLCJtaW1lVHlwZSI6ImFwcGxpY2F0aW9uL2pz"
    "b24ifSwiYWNjZXB0cyI6W3sic2NoZW1lIjoiZXhhY3QiLCJuZXR3b3JrIjoiZWlwMTU1OjE5NiIs"
    "ImFtb3VudCI6IjI1MDAwMCIsImFzc2V0IjoiMHg3NzlkZWQwYzllMTAyMjIyNWY4ZTA2MzBiMzVh"
    "OWI1NGJlNzEzNzM2IiwicGF5VG8iOiIweGU3YmJiMTk3ODI3MDQ4YmE4ZmE3ZTkwOGVjODcxYjgw"
    "NTY4ZGJjMjUiLCJtYXhUaW1lb3V0U2Vjb25kcyI6MzAwLCJleHRyYSI6eyJuYW1lIjoiVVNE4oKu"
    "MCIsInZlcnNpb24iOiIxIn19XX0="
)

NEWSLIQUID_HEADER = (
    "eyJ4NDAyVmVyc2lvbiI6MiwiZXJyb3IiOiJQYXltZW50IHJlcXVpcmVkIiwicmVzb3VyY2UiOnsi"
    "dXJsIjoiaHR0cHM6Ly94NDAyLjY1NTEuaW8vb2t4L25ld3Nfc2VhcmNoIiwiZGVzY3JpcHRpb24i"
    "OiJPcGVuTmV3cyBtZXRlcmVkIHNraWxsOiBuZXdzX3NlYXJjaCIsIm1pbWVUeXBlIjoiIn0sImFj"
    "Y2VwdHMiOlt7InNjaGVtZSI6ImV4YWN0IiwibmV0d29yayI6ImVpcDE1NToxOTYiLCJhbW91bnQi"
    "OiIyMDAwIiwiYXNzZXQiOiIweDc3OWRlZDBjOWUxMDIyMjI1ZjhlMDYzMGIzNWE5YjU0YmU3MTM3"
    "MzYiLCJwYXlUbyI6IjB4MmVjMzEwNjhkNjQ1MTUxMDg0MTU3ODk4MTA4MTEyNGEwZjc1Y2M2NCIs"
    "Im1heFRpbWVvdXRTZWNvbmRzIjozMDAsImV4dHJhIjp7Im5hbWUiOiJVU0Tigq4wIiwidmVyc2lv"
    "biI6IjEifX1dfQ=="
)

# oklink is body-only — no PAYMENT-REQUIRED header at all, captured verbatim.
OKLINK_BODY = {
    "x402Version": 2,
    "resource": {
        "url": "https://www.oklink.com/api/v5/explorer/mcp/x402/get_chain_info",
        "mimeType": "application/json",
    },
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:196",
            "amount": "15",
            "payTo": "0xa7e37604ebab94408159e405033a455f820fd987",
            "maxTimeoutSeconds": 86400,
            "asset": "0x779ded0c9e1022225f8e0630b35a9b54be713736",
            "extra": {"transferMethod": "eip3009", "name": "USD₮0", "symbol": "USDT", "version": "1"},
        },
        {
            "scheme": "aggr_deferred",
            "network": "eip155:196",
            "amount": "15",
            "payTo": "0xa7e37604ebab94408159e405033a455f820fd987",
            "maxTimeoutSeconds": 86400,
            "asset": "0x779ded0c9e1022225f8e0630b35a9b54be713736",
            "extra": {"transferMethod": "eip3009", "name": "USD₮0", "symbol": "USDT", "version": "1"},
        },
    ],
}

ALLOWED_NETWORKS = ("base-sepolia", "eip155:84532", "mock")


def _decode_header(b64: str) -> dict:
    return json.loads(base64.b64decode(b64))


def _resp(*, status=402, headers=None, json_body=None) -> httpx.Response:
    content = json.dumps(json_body).encode() if json_body is not None else b"{}"
    return httpx.Response(status, headers=headers or {}, content=content,
                          request=httpx.Request("GET", "https://example.test/"))


# --- extract_challenge_payload: header-first, body-fallback -----------------

class TestExtractChallengePayload:
    def test_header_only_empty_body(self):
        resp = _resp(headers={"PAYMENT-REQUIRED": BRANDCANVAS_HEADER}, json_body={})
        payload, source = extract_challenge_payload(resp)
        assert source == "header"
        assert payload["x402Version"] == 2
        assert payload["accepts"][0]["network"] == "eip155:196"

    def test_body_only_no_header(self):
        resp = _resp(headers={}, json_body=OKLINK_BODY)
        payload, source = extract_challenge_payload(resp)
        assert source == "body"
        assert payload["accepts"][1]["scheme"] == "aggr_deferred"

    def test_corrupt_header_falls_back_to_body(self):
        resp = _resp(headers={"PAYMENT-REQUIRED": "not-valid-base64!!"}, json_body=OKLINK_BODY)
        payload, source = extract_challenge_payload(resp)
        assert source == "body"
        assert payload["x402Version"] == 2

    def test_neither_header_nor_body_json(self):
        resp = httpx.Response(402, content=b"not json",
                              request=httpx.Request("GET", "https://example.test/"))
        assert extract_challenge_payload(resp) is None


# --- parse_challenge_payload: the four real fixtures ------------------------

class TestRealFixtures:
    @pytest.mark.parametrize("header,expected_amount,expected_scheme_count", [
        (BRANDCANVAS_HEADER, "500000", 1),
        (PIXELBRIEF_HEADER, "250000", 1),
        (NEWSLIQUID_HEADER, "2000", 1),
    ])
    def test_header_only_fixtures_normalize(self, header, expected_amount, expected_scheme_count):
        payload = _decode_header(header)
        challenge = parse_challenge_payload(payload, source="header", verb="GET")
        assert challenge.version == 2
        assert challenge.source == "header"
        req = challenge.selected
        assert req.scheme == "exact"
        assert req.network == "eip155:196"
        assert req.chain_id == 196
        assert req.amount_units == expected_amount
        assert req.asset == "0x779ded0c9e1022225f8e0630b35a9b54be713736"
        assert len(challenge.alternatives) == expected_scheme_count - 1

    def test_oklink_body_only_multi_scheme_prefers_exact(self):
        challenge = parse_challenge_payload(OKLINK_BODY, source="body", verb="POST")
        assert challenge.version == 2
        assert challenge.source == "body"
        assert challenge.verb == "POST"
        assert challenge.selected.scheme == "exact"
        assert [a.scheme for a in challenge.alternatives] == ["aggr_deferred"]
        assert challenge.selected.chain_id == 196
        assert challenge.resource_url == (
            "https://www.oklink.com/api/v5/explorer/mcp/x402/get_chain_info")

    @pytest.mark.parametrize("header", [BRANDCANVAS_HEADER, PIXELBRIEF_HEADER, NEWSLIQUID_HEADER])
    def test_all_header_fixtures_classify_unsupported(self, header):
        payload = _decode_header(header)
        challenge = parse_challenge_payload(payload, source="header", verb="GET")
        outcome, msg = classify_payability(
            challenge, allowed_networks=ALLOWED_NETWORKS,
            payer_label="our Base Sepolia USDC payer")
        assert outcome == "unsupported_network"
        assert "eip155:196" in msg
        assert "our Base Sepolia USDC payer" in msg

    def test_oklink_classifies_unsupported_naming_network(self):
        challenge = parse_challenge_payload(OKLINK_BODY, source="body", verb="POST")
        outcome, msg = classify_payability(
            challenge, allowed_networks=ALLOWED_NETWORKS,
            payer_label="our Base Sepolia USDC payer")
        assert outcome == "unsupported_network"
        assert "eip155:196" in msg
        assert "0x779ded0c9e1022225f8e0630b35a9b54be713736" in msg


# --- v1 legacy fixture -------------------------------------------------------

class TestV1Fixture:
    def test_v1_body_normalizes(self):
        from preflight.x402kit import build_challenge
        body = build_challenge(
            pay_to="0x2f7cF9d979A98d0C4Cd2c92c8DC0d9DFf4a04d2A",
            amount_usdt=0.05, network="mock",
            resource="http://t/mcp/", description="v1 fixture",
        )
        assert body["x402Version"] == 1
        challenge = parse_challenge_payload(body, source="body", verb="POST")
        assert challenge.version == 1
        req = challenge.selected
        assert req.scheme == "exact"
        assert req.network == "mock"
        assert req.chain_id is None  # "mock" isn't CAIP-2 — no chain id to parse
        assert req.amount_units == "50000"
        outcome, _ = classify_payability(challenge, allowed_networks=ALLOWED_NETWORKS)
        assert outcome == "payable"


# --- malformed challenges: recover what we can, name what's missing --------

class TestMalformed:
    def test_missing_accepts_entirely(self):
        with pytest.raises(ChallengeParseError) as exc:
            parse_challenge_payload({"x402Version": 2}, source="body", verb="GET")
        assert exc.value.missing == "accepts"

    def test_empty_accepts_list(self):
        with pytest.raises(ChallengeParseError) as exc:
            parse_challenge_payload({"x402Version": 2, "accepts": []}, source="body", verb="GET")
        assert exc.value.missing == "accepts"

    def test_single_object_instead_of_list_is_recovered(self):
        payload = {
            "x402Version": 2,
            "accepts": {  # a bare object, not a list — real-world sloppiness
                "scheme": "exact", "network": "eip155:196", "amount": "100",
                "payTo": "0xdead", "asset": "0xbeef",
            },
        }
        challenge = parse_challenge_payload(payload, source="body", verb="GET")
        assert challenge.selected.scheme == "exact"
        assert challenge.selected.amount_units == "100"

    def test_nested_accepts_list_is_flattened(self):
        payload = {
            "x402Version": 2,
            "accepts": [[
                {"scheme": "exact", "network": "eip155:196", "amount": "100", "payTo": "0xdead"},
            ]],
        }
        challenge = parse_challenge_payload(payload, source="body", verb="GET")
        assert challenge.selected.amount_units == "100"

    def test_missing_pay_to_and_asset_recovered_not_fatal(self):
        """payTo/asset are useful but not required to recognize a scheme —
        only scheme/network/amount are load-bearing for recovery."""
        payload = {
            "x402Version": 2,
            "accepts": [{"scheme": "exact", "network": "eip155:196", "amount": "100"}],
        }
        challenge = parse_challenge_payload(payload, source="body", verb="GET")
        assert challenge.selected.pay_to is None
        assert challenge.selected.asset is None
        assert challenge.selected.amount_units == "100"

    def test_entry_missing_scheme_names_it(self):
        payload = {"accepts": [{"network": "eip155:196", "amount": "100"}]}
        with pytest.raises(ChallengeParseError) as exc:
            parse_challenge_payload(payload, source="body", verb="GET")
        assert exc.value.missing == "scheme"

    def test_entry_missing_network_names_it(self):
        payload = {"accepts": [{"scheme": "exact", "amount": "100"}]}
        with pytest.raises(ChallengeParseError) as exc:
            parse_challenge_payload(payload, source="body", verb="GET")
        assert exc.value.missing == "network"

    def test_entry_missing_amount_names_it(self):
        payload = {"accepts": [{"scheme": "exact", "network": "eip155:196"}]}
        with pytest.raises(ChallengeParseError) as exc:
            parse_challenge_payload(payload, source="body", verb="GET")
        assert exc.value.missing == "amount"

    def test_non_dict_payload(self):
        with pytest.raises(ChallengeParseError):
            parse_challenge_payload([1, 2, 3], source="body", verb="GET")  # type: ignore[arg-type]


# --- header/response-header naming per detected version --------------------

class TestHeaderSelection:
    def test_v1_uses_x_payment(self):
        assert payment_header_name(1) == "X-PAYMENT"
        assert settlement_header_name(1) == "X-PAYMENT-RESPONSE"

    def test_v2_uses_payment_signature(self):
        assert payment_header_name(2) == "PAYMENT-SIGNATURE"
        assert settlement_header_name(2) == "PAYMENT-RESPONSE"


# --- fetch_with_verb_fallback: redirects + verb switching, replayed live ----

class TestFetchWithVerbFallback:
    def test_pixelbrief_redirect_then_402_header(self):
        """PixelBrief 307s bare-domain GET to the www subdomain before quoting."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "pixelbrief.tech":
                return httpx.Response(307, headers={
                    "location": "https://www.pixelbrief.tech/v1/brand-kit"})
            return httpx.Response(402, headers={"PAYMENT-REQUIRED": PIXELBRIEF_HEADER},
                                  json={})
        transport = httpx.MockTransport(handler)

        async def run():
            async with httpx.AsyncClient(transport=transport) as http:
                return await fetch_with_verb_fallback(
                    http, "https://pixelbrief.tech/v1/brand-kit", method="GET")
        fetched = asyncio.run(run())
        assert fetched.verb == "GET"
        assert fetched.response.status_code == 402
        payload, source = extract_challenge_payload(fetched.response)
        assert source == "header"
        assert payload["accepts"][0]["amount"] == "250000"

    def test_oklink_get_405_then_post_402(self):
        """oklink 405s a GET probe; the challenge only shows up on POST."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(405, json={"code": "405", "msg": "Method Not Allowed"})
            return httpx.Response(402, json=OKLINK_BODY)
        transport = httpx.MockTransport(handler)

        async def run():
            async with httpx.AsyncClient(transport=transport) as http:
                return await fetch_with_verb_fallback(
                    http, "https://www.oklink.com/api/v5/explorer/mcp/x402/get_chain_info",
                    method="GET")
        fetched = asyncio.run(run())
        assert fetched.verb == "POST"
        assert fetched.response.status_code == 402
        payload, source = extract_challenge_payload(fetched.response)
        assert source == "body"
        assert payload["accepts"][1]["scheme"] == "aggr_deferred"

    def test_brandcanvas_and_newsliquid_plain_get(self):
        for header in (BRANDCANVAS_HEADER, NEWSLIQUID_HEADER):
            def handler(request: httpx.Request, header=header) -> httpx.Response:
                return httpx.Response(402, headers={"PAYMENT-REQUIRED": header}, json={})
            transport = httpx.MockTransport(handler)

            async def run():
                async with httpx.AsyncClient(transport=transport) as http:
                    return await fetch_with_verb_fallback(
                        http, "https://example.test/resource", method="GET")
            fetched = asyncio.run(run())
            assert fetched.verb == "GET"
            assert fetched.response.status_code == 402


# --- End-to-end through run_preflight: real ASPs are plain HTTP, not MCP ---
# These exercise the actual C1/C2/C4/C5/C6 wiring against a target that has
# no MCP handshake and no declared paid_tool — exactly BrandCanvas/PixelBrief/
# Newsliquid/oklink's real shape — not just the parsing primitives above.

async def _handle_lifespan(scope, receive, send) -> bool:
    """True if this scope was a lifespan event (and has been fully handled)."""
    if scope["type"] != "lifespan":
        return False
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return True


class _HeaderChallengeApp:
    """A minimal plain-HTTP ASP: any GET gets the BrandCanvas-style 402."""

    async def __call__(self, scope, receive, send):
        if await _handle_lifespan(scope, receive, send):
            return
        assert scope["type"] == "http"
        body = json.dumps({}).encode()
        await send({
            "type": "http.response.start", "status": 402,
            "headers": [(b"content-type", b"application/json"),
                       (b"payment-required", BRANDCANVAS_HEADER.encode())],
        })
        await send({"type": "http.response.body", "body": body})


class _VerbFallbackChallengeApp:
    """A minimal plain-HTTP ASP: GET 405s, POST returns the oklink-style
    body-only 402 (no PAYMENT-REQUIRED header at all)."""

    async def __call__(self, scope, receive, send):
        if await _handle_lifespan(scope, receive, send):
            return
        assert scope["type"] == "http"
        if scope["method"] == "GET":
            body = json.dumps({"code": "405", "msg": "Method Not Allowed"}).encode()
            await send({"type": "http.response.start", "status": 405,
                       "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return
        body = json.dumps(OKLINK_BODY).encode()
        await send({"type": "http.response.start", "status": 402,
                   "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


def _by_id(report):
    return {r.id: r for r in report.results}


class TestRunPreflightAgainstRealShapeASP:
    def test_header_only_asp_no_paid_tool_degrades_gracefully(self, server_factory):
        from preflight.runner import run_preflight

        with server_factory(_HeaderChallengeApp(), 8951):
            report = asyncio.run(run_preflight("http://127.0.0.1:8951/", {}))

        results = _by_id(report)
        assert results["C1"].status.value == "pass"
        assert results["C2"].status.value == "skip"
        assert "not an MCP endpoint" in results["C2"].summary
        assert results["C4"].status.value == "warn"
        assert "eip155:196" in results["C4"].summary
        assert results["C4"].evidence["verb"] == "GET"
        assert results["C4"].evidence["x402_version"] == 2
        assert results["C5"].status.value == "skip"  # no declared price
        assert results["C6"].status.value == "skip"  # mainnet refused by payer policy
        assert report.overall == "PASS"  # nothing here actually FAILed

    def test_verb_fallback_asp_reports_post_and_multi_scheme(self, server_factory):
        from preflight.runner import run_preflight

        with server_factory(_VerbFallbackChallengeApp(), 8952):
            report = asyncio.run(run_preflight("http://127.0.0.1:8952/", {}))

        results = _by_id(report)
        assert results["C4"].status.value == "warn"
        assert results["C4"].evidence["verb"] == "POST"
        assert results["C4"].evidence["source"] == "body"
        assert results["C4"].evidence["scheme"] == "exact"
        assert results["C4"].evidence["alternative_schemes"] == ["aggr_deferred"]
        assert report.overall == "PASS"
