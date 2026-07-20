"""paid_tool inference + differentiated failure-reason reporting.

Covers: (1) omitted paid_tool with exactly one inferable common tool,
(2) omitted paid_tool with no unambiguous common tool, (3) every target dead
(paid_tool supplied — isolates the "unreachable" path from the "no paid tool"
path), (4) a target that answers without demanding payment at all (free tool,
not a broken paywall).
"""
import asyncio
import dataclasses

from fastmcp import FastMCP

from fixtures.broken_bazaar.vendor_sim import create_app as vendor
from preflight import config as config_module
from preflight.bench import compare_services, comparison_markdown
from preflight.models import Status
from preflight.runner import run_preflight


def _urls(ports):
    return [f"http://127.0.0.1:{p}/mcp/" for p in ports]


def _with_wake_timeout(monkeypatch, timeout_s: float):
    monkeypatch.setattr(config_module, "settings",
                        dataclasses.replace(config_module.settings, wake_timeout_s=timeout_s))


class _WithHealthz:
    """Adds a plain 200 /healthz to a bare FastMCP http_app, so the wake phase
    treats it like any other live service (mirrors PaywallASGI's own handling)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["method"] in ("GET", "HEAD")
                and scope.get("path", "").rstrip("/") == "/healthz"):
            body = b'{"ok": true}'
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json"),
                                   (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body",
                        "body": body if scope["method"] != "HEAD" else b""})
            return
        return await self.app(scope, receive, send)


def _stub_app(tool_names: list[str]):
    """A minimal unpaywalled MCP app exposing exactly `tool_names` — every call
    succeeds with 200, no x402 challenge. Used for tool-inference and
    free-service tests."""
    mcp = FastMCP("Stub")
    for name in tool_names:
        async def _fn() -> str:
            return "ok"
        mcp.tool(_fn, name=name)
    inner = mcp.http_app(path="/mcp/", stateless_http=True, json_response=True)
    wrapped = _WithHealthz(inner)
    wrapped.lifespan = inner.lifespan
    return wrapped


def test_infer_single_common_tool(server_factory):
    """Both targets expose only {ping, market_pulse}; market_pulse is the only
    non-utility tool in common, so it should be inferred and the comparison
    should run exactly as if it had been supplied."""
    with server_factory(vendor(price=0.02, latency_ms=10, richness=0), 8881), \
         server_factory(vendor(price=0.05, latency_ms=60, richness=2), 8882):
        comp = asyncio.run(compare_services(_urls([8881, 8882]), task="feed"))

    assert comp.no_paid_tool is False
    assert comp.paid_tool == "market_pulse"
    assert comp.paid_tool_inferred is True
    assert len(comp.candidates) == 2
    assert all(c.purchased for c in comp.candidates)
    assert comp.winner_url is not None

    md = comparison_markdown(comp, "http://base")
    assert "inferred as `market_pulse`" in md
    assert "No paid tool was named" not in md


def test_infer_ambiguous_no_common_tool(server_factory):
    """Two targets with disjoint non-utility tool sets: nothing to infer.
    No probing/purchase should be attempted, and the report must say so
    explicitly rather than claiming 'no usable service'."""
    with server_factory(_stub_app(["ping", "market_pulse"]), 8883), \
         server_factory(_stub_app(["ping", "weather"]), 8884):
        comp = asyncio.run(compare_services(_urls([8883, 8884]), task="feed"))

    assert comp.no_paid_tool is True
    assert comp.paid_tool is None
    assert comp.paid_tool_inferred is False
    assert comp.candidates == []  # nothing was probed or purchased
    assert comp.winner_url is None
    assert comp.total_spend_usdt == 0.0

    tools = comp.target_tools
    assert sorted(tools[_urls([8883])[0]]) == ["market_pulse", "ping"]
    assert sorted(tools[_urls([8884])[0]]) == ["ping", "weather"]

    md = comparison_markdown(comp, "http://base")
    assert "No paid tool was named, and none could be inferred" in md
    assert "No usable service among the candidates" not in md
    assert "market_pulse" in md and "weather" in md


def test_all_targets_dead_reports_unreachable(monkeypatch):
    """paid_tool is supplied explicitly, isolating this from the no-paid-tool
    path: every target is genuinely offline, so 'no usable service' is the
    correct, honest message here."""
    _with_wake_timeout(monkeypatch, 1.2)
    comp = asyncio.run(compare_services(
        # nothing listens on either port
        _urls([8997, 8998]), paid_tool="market_pulse", task="feed"))

    assert comp.no_paid_tool is False
    assert comp.paid_tool == "market_pulse"
    assert len(comp.candidates) == 2
    assert all(not c.reachable and not c.usable for c in comp.candidates)
    assert comp.winner_url is None

    md = comparison_markdown(comp, "http://base")
    assert "No usable service among the candidates" in md


def test_free_target_reported_as_free_not_broken(server_factory):
    """One target genuinely paywalls market_pulse; the other answers it
    directly with 200 (no 402) — a free service, not a broken one. The two
    must read differently in the report, and C5/C6 must not print the generic
    hardcoded 'no challenge captured in C4' line."""
    with server_factory(vendor(price=0.05, latency_ms=10, richness=1), 8885), \
         server_factory(_stub_app(["ping", "market_pulse"]), 8886):
        comp = asyncio.run(compare_services(
            _urls([8885, 8886]), paid_tool="market_pulse", task="feed"))

        # Confirm at the single-target check level too, while the server is
        # still up: C4 is a WARN (not a FAIL — a free tool isn't a broken
        # paywall), and C5/C6 quote C4's real reason instead of a generic line.
        report = asyncio.run(run_preflight(
            "http://127.0.0.1:8886/mcp/", {"paid_tool": "market_pulse"}))

    paid = [c for c in comp.candidates if "8885" in c.target_url][0]
    free = [c for c in comp.candidates if "8886" in c.target_url][0]

    assert paid.usable
    assert not free.usable
    assert any("appears to be free" in n for n in free.notes)
    assert not any("no challenge captured in C4" in n for n in free.notes)

    by_id = {r.id: r for r in report.results}
    assert by_id["C4"].status == Status.WARN
    assert "this service appears to be free — no payment required" in by_id["C4"].summary
    assert by_id["C5"].status == Status.SKIP
    assert "appears to be free" in by_id["C5"].summary
    assert by_id["C6"].status == Status.SKIP
    assert "appears to be free" in by_id["C6"].summary
