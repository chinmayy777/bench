"""ASP PreFlight service.

FastMCP tools (the A2MCP surface) + human-readable report permalinks in one
process. Free to call — the paid tier is post-hackathon roadmap.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import Field

from x402 import x402ResourceServer
from x402.http.middleware import PaymentMiddlewareASGI
from x402.http.types import PaymentOption, RouteConfig
from x402.mechanisms.evm.exact import register_exact_evm_server

from .config import settings
from .models import Status
from .runner import run_preflight, summary_markdown
from .ssrf import TargetRejected
from .store import load_report
from .x402_seller import LocalZeroFeeFacilitator

# The one thing Tender itself sells (nothing, technically): a zero-amount x402
# handshake on eip155:196 so the marketplace's validator sees a real 402
# challenge on /mcp, matching every other listed ASP's wire behavior. See
# x402_seller.py for why this is a local facilitator, not OKXFacilitatorClient.
TENDER_NETWORK = "eip155:196"
TENDER_ASSET = "0x779ded0c9e1022225f8e0630b35a9b54be713736"  # USDT on X Layer
TENDER_PAY_TO = "0xAB213fEcccEd629E48A8a4f54C0B636B5ae01eEc"  # agent 6337's registered wallet

logging.basicConfig(level=logging.INFO, format='{"t":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
log = logging.getLogger("preflight")

_HERE = pathlib.Path(__file__).parent
jinja = Environment(loader=FileSystemLoader(_HERE / "templates"),
                    autoescape=select_autoescape(["html"]))

mcp = FastMCP(
    "Tender",
    instructions=(
        "Value comparison for paid agent services (A2MCP). Give Tender several "
        "ASP endpoints that do the same job; it makes one real, settled x402 "
        "purchase from each, measures price, latency, and delivery, and returns "
        "a ranked scorecard naming the best value — with an on-chain transaction "
        "as proof for every purchase. Call compare_services with the competing "
        "endpoints. Let your agent buy the best, not the first."
    ),
)


@mcp.tool
async def preflight_run(
    target_url: Annotated[str, Field(description="Public MCP endpoint URL to test, e.g. https://api.example.com/mcp/")],
    paid_tool: Annotated[str | None, Field(description="Name of the x402-paid tool to test-purchase")] = None,
    price_usdt: Annotated[float | None, Field(description="The price your listing declares, in USDT")] = None,
    tools: Annotated[list[str] | None, Field(description="Tool names your listing declares")] = None,
    sample_args: Annotated[dict | None, Field(description="Arguments to use when calling the paid tool")] = None,
) -> str:
    """Run the full 9-point PreFlight suite against a target ASP endpoint."""
    claims = {k: v for k, v in {
        "paid_tool": paid_tool, "price_usdt": price_usdt,
        "tools": tools, "sample_args": sample_args or {},
    }.items() if v is not None}
    try:
        report = await run_preflight(target_url, claims)
    except TargetRejected as e:
        return f"❌ Target rejected before any check ran: {e}"
    return summary_markdown(report, settings.base_url)


@mcp.tool
async def get_report(report_id: Annotated[str, Field(description="Report id from a previous run")]) -> str:
    """Fetch the scorecard for a previous PreFlight run."""
    report = load_report(report_id)
    if report is None:
        return f"No report with id {report_id!r}."
    return summary_markdown(report, settings.base_url)


@mcp.tool
async def compare_services(
    targets: Annotated[list[str], Field(description="2-5 ASP MCP endpoint URLs that do the same job")],
    paid_tool: Annotated[str | None, Field(description="The paid tool name to buy from each")] = None,
    price_usdt: Annotated[float | None, Field(description="Expected price, if you want price-integrity flagged")] = None,
    sample_args: Annotated[dict | None, Field(description="Arguments to call the paid tool with")] = None,
    task: Annotated[str, Field(description="Short label for what these services do")] = "",
) -> str:
    """Buy from several competing ASPs and rank them by value (price, latency, delivery)."""
    from .bench import compare_services as _compare, comparison_markdown
    try:
        comp = await _compare(targets, paid_tool=paid_tool, price_usdt=price_usdt,
                              sample_args=sample_args, task=task)
    except (ValueError, TargetRejected) as e:
        return f"❌ Comparison could not run: {e}"
    return comparison_markdown(comp, settings.base_url)


mcp_app = mcp.http_app(path="/", stateless_http=True, json_response=True)

import contextlib


@contextlib.asynccontextmanager
async def lifespan(app):
    # seed the permanent demo comparison once per boot (idempotent)
    try:
        from .demo_seed import ensure_demo_comparison
        ensure_demo_comparison()
    except Exception as e:  # never block startup on the seed
        log.warning("demo seed skipped: %s", e)
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(title="Tender", lifespan=lifespan)

# Seller-side x402: gate the whole /mcp mount behind a zero-amount 402
# handshake, matching how other free ASPs on the marketplace (e.g. TRACE,
# agent #7515) answer unpaid calls. See x402_seller.py for why verify/settle
# run against an in-process facilitator rather than OKX's hosted one.
_x402_server = x402ResourceServer(LocalZeroFeeFacilitator(network=TENDER_NETWORK))
register_exact_evm_server(_x402_server, networks=TENDER_NETWORK)
_x402_routes = {
    # Every verb, not just POST — the marketplace's x402 validator probes
    # with a bare GET and rejects any endpoint that doesn't 402 on it (TRACE,
    # the working reference, gates GET too). See mcp_get_hint() below for
    # what a *paid* GET gets back, since GET carries no JSON-RPC call to
    # actually fulfill.
    "/mcp*": RouteConfig(
        accepts=[PaymentOption(
            scheme="exact",
            network=TENDER_NETWORK,
            pay_to=TENDER_PAY_TO,
            price={"amount": "0", "asset": TENDER_ASSET,
                   "extra": {"name": "USD₮0", "version": "1"}},
            max_timeout_seconds=300,
        )],
        resource=f"{settings.base_url}/mcp/",
        description="Tender — free value comparison for paid agent services (A2MCP)",
        mime_type="application/json",
    )
}
app.add_middleware(PaymentMiddlewareASGI, routes=_x402_routes, server=_x402_server)


async def _tool_schemas() -> list[dict]:
    """The live tool list/schemas, straight from the same FastMCP registry the
    real MCP transport serves — one source of truth, no hand-duplicated copy
    to drift out of sync."""
    tools = await mcp.list_tools()
    return [{"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in tools]


async def _discovery_doc() -> dict:
    """Machine-readable answer to "how do I call Tender": buyer-side, free,
    POST-JSON-RPC-MCP — served at /.well-known/mcp.json, /.well-known/agent.json,
    and /about, so reviewers stop guessing (probing dead discovery paths or
    sending empty args)."""
    return {
        "name": "Tender",
        "role": "buyer",
        "pricing": {
            "model": "free",
            "amount_usdt": 0,
            "note": "Tender charges nothing to call, but — like every other ASP "
                    "on the marketplace — it does issue a standard x402 402 "
                    "handshake on every /mcp call; the quoted amount is always "
                    "0, so settlement is a no-op and the call completes at zero "
                    "cost. Tender is also the one paying OTHER ASPs' real, "
                    "non-zero 402s on the caller's behalf when comparing them.",
        },
        "summary": "Tender is a free, buyer-side x402 comparison agent. Give it "
                   "several ASP endpoints that do the same job; it pays their "
                   "real x402 challenges, measures price, latency, and delivery, "
                   "and returns a ranked scorecard naming the best value. Calling "
                   "Tender itself is free — its own 402 challenge always quotes "
                   "amount 0 — it does not sell a paid service.",
        "protocol": {
            "type": "mcp",
            "transport": "streamable-http",
            "endpoint": f"{settings.base_url}/mcp/",
            "method": "POST",
            "content_type": "application/json",
            "framing": "JSON-RPC 2.0",
            "example": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        },
        "tools": await _tool_schemas(),
        "links": {
            "about": f"{settings.base_url}/about",
            "discovery_mcp": f"{settings.base_url}/.well-known/mcp.json",
            "discovery_agent": f"{settings.base_url}/.well-known/agent.json",
            "healthz": f"{settings.base_url}/healthz",
            "landing": f"{settings.base_url}/",
        },
    }


@app.get("/mcp/")
async def mcp_get_hint() -> JSONResponse:
    """Reached ONLY by a GET whose x402 payment has already been verified —
    every unpaid GET to /mcp/ is intercepted by the payment middleware above
    and answered with the 402 challenge before Starlette routing ever runs
    (the gate covers every verb now, not just POST, matching TRACE). A GET
    carries no JSON-RPC body/method, so there is no tool call to fulfill
    here; this just acknowledges the (zero-cost) settlement and points paid
    callers at the real call shape. Registered ahead of the /mcp mount so it
    wins for GET specifically; POST still falls through to the mounted MCP
    transport, unchanged."""
    return JSONResponse(status_code=200, content={
        "ok": True,
        "message": "Payment verified. GET carries no JSON-RPC request to "
                   "fulfill — use POST for tool calls.",
        "example": f'curl -X POST {settings.base_url}/mcp/ '
                   '-H "Content-Type: application/json" '
                   '-d \'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\'',
        "discovery": f"{settings.base_url}/.well-known/mcp.json",
    })


class _MCPAcceptHeaderShim:
    """FastMCP's underlying streamable-HTTP transport (mcp.server.streamable_http)
    406s any POST whose Accept header doesn't already contain "application/json"
    — even though it would otherwise answer with a perfectly normal JSON-RPC
    result. There is no FastMCP/http_app() config flag to relax this (checked:
    it's hardcoded in _validate_accept_header, several layers below FastMCP).

    A known-working free ASP on the marketplace (ScoutGate) tolerates a
    missing Accept header entirely and just answers — naive probers that
    don't set MCP's exact header were getting a 406 from Tender instead of
    the same clean 200. Widen the header before it reaches the transport,
    but only when it wouldn't already satisfy the check, so callers that
    already negotiate correctly — including real Accept: text/event-stream
    clients doing SSE streaming — see no change at all."""

    _CANONICAL_ACCEPT = b"application/json, text/event-stream"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST":
            headers = list(scope.get("headers", []))
            accept = next((v for k, v in headers if k.lower() == b"accept"), b"")
            if not self._accepts_json(accept):
                headers = [(k, v) for k, v in headers if k.lower() != b"accept"]
                headers.append((b"accept", self._CANONICAL_ACCEPT))
                scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)

    @staticmethod
    def _accepts_json(accept_header: bytes) -> bool:
        types = [t.strip() for t in accept_header.decode("latin-1").split(",")]
        return any(t.startswith("application/json") for t in types)


app.mount("/mcp", _MCPAcceptHeaderShim(mcp_app))
app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")


@app.get("/.well-known/mcp.json")
async def well_known_mcp() -> JSONResponse:
    return JSONResponse(await _discovery_doc())


@app.get("/.well-known/agent.json")
async def well_known_agent() -> JSONResponse:
    return JSONResponse(await _discovery_doc())


@app.get("/about")
async def about() -> JSONResponse:
    return JSONResponse(await _discovery_doc())


async def _healthz(request: Request) -> JSONResponse:
    resp = JSONResponse({"ok": True, "payer_mode": settings.payer_mode})
    if request.method == "HEAD":
        resp.body = b""  # same status/headers as GET, no body, per HTTP spec
    return resp


# Registered as two routes (not one api_route(methods=["GET","HEAD"])) with
# distinct operation_ids — sharing one auto-generated id across two methods
# on the same path is what FastAPI was warning about at startup.
app.add_api_route("/healthz", _healthz, methods=["GET"], operation_id="healthz_get")
app.add_api_route("/healthz", _healthz, methods=["HEAD"], operation_id="healthz_head")


@app.post("/api/run")
async def api_run(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        report = await run_preflight(body["target_url"], body.get("claims") or {})
    except TargetRejected as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"report_id": report.id, "overall": report.overall,
                         "url": f"{settings.base_url}/report/{report.id}"})


@app.get("/compare/{comp_id}", response_class=HTMLResponse)
async def comparison_page(comp_id: str) -> HTMLResponse:
    from .store import load_comparison
    comp = load_comparison(comp_id)
    if comp is None:
        raise HTTPException(404, "no such comparison")
    html = jinja.get_template("comparison.html").render(c=comp)
    return HTMLResponse(html)


@app.get("/report/{report_id}", response_class=HTMLResponse)
async def report_page(report_id: str) -> HTMLResponse:
    report = load_report(report_id)
    if report is None:
        raise HTTPException(404, "no such report")
    counts = {s.value: sum(1 for r in report.results if r.status == s) for s in Status}
    html = jinja.get_template("report.html").render(r=report, counts=counts)
    return HTMLResponse(html)


@app.get("/api/demo-run")
async def demo_run(target_url: str, paid_tool: str = "market_pulse",
                   price_usdt: float = 0.05) -> HTMLResponse:
    """Browser-triggerable run (GET). Enabled only when DEMO_TRIGGER=1.

    Lets you kick off a real run from the address bar during setup without a
    local Python env. Disable after the demo by removing the env var.
    """
    import os
    if os.getenv("DEMO_TRIGGER", "0").lower() not in {"1", "true", "yes"}:
        raise HTTPException(404, "not found")
    try:
        report = await run_preflight(target_url, {
            "paid_tool": paid_tool, "price_usdt": price_usdt,
            "tools": ["ping", "market_pulse"],
        })
    except TargetRejected as e:
        raise HTTPException(400, str(e))
    link = f"{settings.base_url}/report/{report.id}"
    return HTMLResponse(
        f'<p>Run complete: <b>{report.overall}</b></p>'
        f'<p>Spend: {report.spend_usdt} USDC · tx refs: {report.tx_refs}</p>'
        f'<p><a href="{link}">{link}</a></p>'
    )


@app.get("/api/demo-compare", response_class=HTMLResponse)
async def demo_compare(targets: str, paid_tool: str = "market_pulse",
                       task: str = "market pulse feed") -> HTMLResponse:
    """Browser-triggerable comparison (GET). Enabled only when DEMO_TRIGGER=1.

    `targets` is a comma-separated list of vendor MCP URLs. Runs the full compare
    (a real purchase from each on testnet) and redirects to the leaderboard page.
    """
    import os
    if os.getenv("DEMO_TRIGGER", "0").lower() not in {"1", "true", "yes"}:
        raise HTTPException(404, "not found")
    url_list = [u.strip() for u in targets.split(",") if u.strip()]
    from .bench import compare_services as _compare
    try:
        comp = await _compare(url_list, paid_tool=paid_tool, task=task)
    except (ValueError, TargetRejected) as e:
        raise HTTPException(400, str(e))
    link = f"{settings.base_url}/compare/{comp.id}"
    rows = "".join(
        f"<li>{c.target_url} — score {c.value_score if c.usable else '—'} "
        f"· {c.price_usdt} USDC · {c.latency_ms}ms · tx {c.tx_ref or '—'}</li>"
        for c in comp.candidates)
    return HTMLResponse(
        f'<p>Comparison complete. Best value: <b>{comp.winner_url or "none"}</b></p>'
        f'<p>Total spend: {comp.total_spend_usdt} USDC</p><ul>{rows}</ul>'
        f'<p><a href="{link}">{link}</a></p>'
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    import os
    sample_tx = os.getenv(
        "SAMPLE_TX",
        "0x2f0ebf349f97b134557172955907438bb0545cc755e9c7d403ec9a4d4f9453df")
    ctx = {
        "listing_url": os.getenv("LISTING_URL", "https://www.okx.ai/"),
        "sample_compare_url": os.getenv("SAMPLE_COMPARE_URL", "/compare/demo"),
        "sample_tx_url": f"https://sepolia.basescan.org/tx/{sample_tx}",
        "sample_tx_short": f"{sample_tx[:22]}…{sample_tx[-6:]}",
    }
    return HTMLResponse(jinja.get_template("landing.html").render(**ctx))
