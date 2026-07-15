"""VendorSim — a configurable paid market-data ASP for Bench comparisons.

Same paid tool name (`market_pulse`) as BrokenBazaar so Bench can compare
like-for-like, but with env-tunable PRICE, LATENCY_MS, and RICHNESS so several
instances present a real value tradeoff:

  cheap+fast+thin   vs   pricier+slower+rich   vs   mid

Reuses the exact PaywallASGI + facilitator machinery from broken_bazaar.
"""
from __future__ import annotations

import json
import os
import time

from fastmcp import FastMCP

from .app import (DEFAULT_PAY_TO, PaywallASGI, DirectSettleFacilitator,
                  MockFacilitator)


def _pulse(richness: int) -> str:
    base = [
        {"pair": "OKB/USDT", "drift_bps": 12, "note": "range-bound"},
        {"pair": "ETH/USDT", "drift_bps": -8, "note": "cooling"},
    ]
    extra = [
        {"pair": "BTC/USDT", "drift_bps": 5, "note": "steady"},
        {"pair": "SOL/USDT", "drift_bps": 21, "note": "momentum"},
        {"pair": "XLAYER/USDT", "drift_bps": 3, "note": "thin book"},
    ]
    signals = base + extra[: max(0, richness)]
    body = {"pulse": "steady", "signals": signals}
    if richness >= 2:
        body["commentary"] = ("Cross-pair momentum diverging; majors cooling while "
                              "mid-caps firm. Liquidity thin on X Layer pairs.")
    body["disclaimer"] = "demo data — VendorSim test fixture"
    return json.dumps(body)


def build_mcp(latency_ms: int, richness: int) -> FastMCP:
    mcp = FastMCP("VendorSim", instructions="Configurable paid data vendor for Bench.")

    @mcp.tool
    def ping() -> str:
        """Free liveness check."""
        return "pong"

    @mcp.tool
    def market_pulse() -> str:
        """Paid: a synthetic market pulse report (x402-gated)."""
        if latency_ms:
            time.sleep(latency_ms / 1000.0)
        return _pulse(richness)

    return mcp


def create_app(*, price: float = 0.05, latency_ms: int = 0, richness: int = 0,
               network: str = "mock", pay_to: str = DEFAULT_PAY_TO,
               facilitator=None, resource: str = "http://vendor.local/mcp/"):
    mcp = build_mcp(latency_ms, richness)
    inner = mcp.http_app(path="/mcp/", stateless_http=True, json_response=True)
    wrapped = PaywallASGI(inner, pay_to=pay_to, network=network, quote_price=price,
                          facilitator=facilitator or MockFacilitator(), resource=resource)
    wrapped.lifespan = inner.lifespan
    wrapped.inner_app = inner
    return wrapped


def env_app():
    mode = os.getenv("FACILITATOR", "mock")
    network = os.getenv("NETWORK", "mock")
    fac = None
    if mode == "direct":
        from preflight.x402kit import KNOWN_NETWORKS
        net = KNOWN_NETWORKS[network]
        fac = DirectSettleFacilitator(
            network=network, relayer_key=os.getenv("RELAYER_PRIVATE_KEY", ""),
            usdc_address=net["asset"], chain_id=net["chain_id"],
            rpc_url=os.getenv("RPC_URL") or None)
    return create_app(
        price=float(os.getenv("PRICE", "0.05")),
        latency_ms=int(os.getenv("LATENCY_MS", "0")),
        richness=int(os.getenv("RICHNESS", "0")),
        network=network, pay_to=os.getenv("PAY_TO", DEFAULT_PAY_TO),
        facilitator=fac, resource=os.getenv("RESOURCE_URL", "http://vendor.local/mcp/"))


app = env_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8810")))
