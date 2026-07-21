"""Non-MCP path for compare_services: plain HTTP x402 resources.

Real marketplace ASPs (BrandCanvas, PixelBrief, Newsliquid, oklink) are not
MCP servers — the URL itself is the purchasable resource. These tests cover:
two plain HTTP targets compared against each other, a mixed MCP+HTTP set, and
a set where every candidate quotes a network/asset the payer can't settle.
"""
from __future__ import annotations

import json

from fixtures.broken_bazaar.vendor_sim import create_app as vendor
from preflight.bench import compare_services, comparison_markdown
from preflight.x402kit import MockFacilitator, build_challenge, parse_challenge


def _urls(ports):
    return [f"http://127.0.0.1:{p}/mcp/" for p in ports]


async def _handle_lifespan(scope, receive, send) -> bool:
    if scope["type"] != "lifespan":
        return False
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return True


async def _send_json(send, status: int, payload: dict, headers: list | None = None) -> None:
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
               "headers": [(b"content-type", b"application/json")] + (headers or [])})
    await send({"type": "http.response.body", "body": body})


class PlainHttpX402App:
    """A minimal plain-HTTP ASP — no MCP handshake at all. Any GET besides
    /healthz is the priced resource itself, x402-gated on the payable 'mock'
    network via the same SDK-backed challenge/facilitator BrokenBazaar uses,
    just without any MCP/JSON-RPC wrapping."""

    def __init__(self, *, price_usdt: float, pay_to: str, content: bytes):
        self.price_usdt = price_usdt
        self.pay_to = pay_to
        self.content = content
        self.facilitator = MockFacilitator()

    async def __call__(self, scope, receive, send):
        if await _handle_lifespan(scope, receive, send):
            return
        assert scope["type"] == "http"
        if scope.get("path", "").rstrip("/") == "/healthz":
            await _send_json(send, 200, {"ok": True})
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        challenge = build_challenge(
            pay_to=self.pay_to, amount_usdt=self.price_usdt, network="mock",
            resource="http://plain-x402.local/resource", description="test resource")
        payment = headers.get("x-payment")
        if not payment:
            await _send_json(send, 402, challenge)
            return
        req = parse_challenge(challenge).accepts[0]
        ok, who_or_reason, tx_ref = self.facilitator.verify(payment, req)
        if not ok:
            challenge["error"] = f"payment rejected: {who_or_reason}"
            await _send_json(send, 402, challenge)
            return
        await send({"type": "http.response.start", "status": 200,
                   "headers": [(b"content-type", b"application/json"),
                              (b"x-payment-response", tx_ref.encode())]})
        await send({"type": "http.response.body", "body": self.content})


class UnpayableHttpApp:
    """A plain-HTTP ASP whose 402 quotes a real-world mainnet network/asset
    (eip155:196, X Layer) that this payer is deliberately never allowed to
    settle — mirrors BrandCanvas/PixelBrief/Newsliquid/oklink exactly."""

    def __init__(self, *, pay_to: str, asset: str, amount: str = "500000"):
        self.pay_to, self.asset, self.amount = pay_to, asset, amount

    async def __call__(self, scope, receive, send):
        if await _handle_lifespan(scope, receive, send):
            return
        assert scope["type"] == "http"
        if scope.get("path", "").rstrip("/") == "/healthz":
            await _send_json(send, 200, {"ok": True})
            return
        challenge = {
            "x402Version": 2,
            "resource": {"url": "https://example.test/resource"},
            "accepts": [{
                "scheme": "exact", "network": "eip155:196", "amount": self.amount,
                "payTo": self.pay_to, "asset": self.asset, "maxTimeoutSeconds": 300,
            }],
        }
        await _send_json(send, 402, challenge)


PAY_TO_A = "0x2f7cF9d979A98d0C4Cd2c92c8DC0d9DFf4a04d2A"
PAY_TO_B = "0xe7bbb197827048ba8fa7e908ec871b80568dbc25"
ASSET_A = "0x779ded0c9e1022225f8e0630b35a9b54be713736"


class TestTwoPlainHttpTargets:
    def test_ranks_two_http_resources_by_value(self, server_factory):
        import asyncio

        with server_factory(PlainHttpX402App(price_usdt=0.02, pay_to=PAY_TO_A,
                                              content=b'{"data": "small"}'), 8961), \
             server_factory(PlainHttpX402App(price_usdt=0.15, pay_to=PAY_TO_B,
                                              content=b'{"data": "much bigger payload here"}'), 8962):
            comp = asyncio.run(compare_services(_urls([8961, 8962]), task="two plain HTTP ASPs"))

        assert comp.no_paid_tool is False
        assert len(comp.candidates) == 2
        assert all(c.shape == "http" for c in comp.candidates)
        assert all(c.purchased for c in comp.candidates)
        assert comp.target_shapes == {_urls([8961])[0]: "http", _urls([8962])[0]: "http"}
        assert comp.winner_url is not None

        md = comparison_markdown(comp, "http://base")
        assert "| HTTP |" in md
        assert "No paid tool was named" not in md


class TestMixedMcpAndHttp:
    def test_mixed_set_compares_across_shapes(self, server_factory):
        import asyncio

        with server_factory(vendor(price=0.05, latency_ms=10, richness=1), 8963), \
             server_factory(PlainHttpX402App(price_usdt=0.02, pay_to=PAY_TO_A,
                                              content=b'{"data": "cheap and small"}'), 8964):
            comp = asyncio.run(compare_services(_urls([8963, 8964]), task="mixed shapes"))

        assert comp.no_paid_tool is False
        # MCP-side inference still worked (single MCP target, market_pulse is
        # its only non-utility tool) even though the other target isn't MCP.
        assert comp.paid_tool == "market_pulse"
        assert len(comp.candidates) == 2

        by_shape = {c.shape: c for c in comp.candidates}
        assert set(by_shape) == {"mcp", "http"}
        assert all(c.purchased for c in comp.candidates)
        assert comp.target_shapes[_urls([8963])[0]] == "mcp"
        assert comp.target_shapes[_urls([8964])[0]] == "http"
        assert comp.winner_url is not None  # a real ranking happened across shapes

        md = comparison_markdown(comp, "http://base")
        assert "| MCP |" in md and "| HTTP |" in md


class TestAllUnpayable:
    def test_all_candidates_unpayable_reported_precisely(self, server_factory):
        import asyncio

        with server_factory(UnpayableHttpApp(pay_to=PAY_TO_A, asset=ASSET_A), 8965), \
             server_factory(UnpayableHttpApp(pay_to=PAY_TO_B, asset=ASSET_A), 8966):
            comp = asyncio.run(compare_services(_urls([8965, 8966]), task="all unpayable"))

        assert comp.no_paid_tool is False
        assert len(comp.candidates) == 2
        assert comp.winner_url is None
        assert all(not c.purchased for c in comp.candidates)
        assert all(not c.usable for c in comp.candidates)
        assert all(c.value_score == 0.0 for c in comp.candidates)  # never scored/ranked
        assert all(c.challenge_outcome == "unsupported_network" for c in comp.candidates)
        assert all(c.network == "eip155:196" for c in comp.candidates)

        md = comparison_markdown(comp, "http://base")
        assert "No candidate is payable" in md
        assert "eip155:196" in md
        assert _urls([8965])[0] in md and _urls([8966])[0] in md
        assert "No usable service among the candidates" not in md
        assert "broken" not in md.lower()
