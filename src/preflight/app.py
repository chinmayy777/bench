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

from .config import settings
from .models import Status
from .runner import run_preflight, summary_markdown
from .ssrf import TargetRejected
from .store import load_report

logging.basicConfig(level=logging.INFO, format='{"t":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
log = logging.getLogger("preflight")

_HERE = pathlib.Path(__file__).parent
jinja = Environment(loader=FileSystemLoader(_HERE / "templates"),
                    autoescape=select_autoescape(["html"]))

mcp = FastMCP(
    "ASP PreFlight",
    instructions=(
        "Commerce-grade preflight checks for paid agent services (A2MCP). "
        "Point preflight_run at your MCP endpoint before you list it: it probes "
        "reachability, handshake, schema-vs-claims, x402 paywall correctness, "
        "price integrity, settled delivery, latency and resilience, then returns "
        "a scorecard with an evidence permalink."
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

app = FastAPI(title="ASP PreFlight", lifespan=mcp_app.lifespan)
app.mount("/mcp", mcp_app)
app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "payer_mode": settings.payer_mode})


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
        "sample_compare_url": os.getenv("SAMPLE_COMPARE_URL", ""),
        "sample_tx_url": f"https://sepolia.basescan.org/tx/{sample_tx}",
        "sample_tx_short": f"{sample_tx[:22]}…{sample_tx[-6:]}",
    }
    return HTMLResponse(jinja.get_template("landing.html").render(**ctx))
