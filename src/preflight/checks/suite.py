"""The nine checks. See package __init__ for the shared contract."""
from __future__ import annotations

import statistics
import time

from x402.schemas.v1 import PaymentRequiredV1

from ..models import CheckResult, Status
from ..payer import PayerRefused
from ..x402kit import parse_challenge, units_to_usdt
from . import RunContext, call_tool_raw, excerpt, mcp_client, timed


def _mk(fn, cid: str, name: str):
    fn.CHECK_ID, fn.CHECK_NAME = cid, name
    return timed(fn)


async def _c1(ctx: RunContext) -> CheckResult:
    t0 = time.perf_counter()
    resp = await ctx.http.get(ctx.target_url, timeout=6.0,
                              headers={"accept": "application/json, text/event-stream"})
    ms = int((time.perf_counter() - t0) * 1000)
    ev = {"status_code": resp.status_code, "first_byte_ms": ms,
          "https": ctx.target_url.startswith("https://"),
          "http_version": resp.http_version}
    if resp.status_code >= 500:
        return CheckResult("C1", _c1.CHECK_NAME, Status.FAIL,
                           f"endpoint returned {resp.status_code}", ev)
    note = "" if ev["https"] else " (http allowed in local mode only)"
    return CheckResult("C1", _c1.CHECK_NAME, Status.PASS,
                       f"reachable in {ms} ms{note}", ev)


async def _c2(ctx: RunContext) -> CheckResult:
    async with mcp_client(ctx) as client:
        tools = await client.list_tools()
    names = sorted(t.name for t in tools)
    ctx.state["actual_tools"] = names
    if not names:
        return CheckResult("C2", _c2.CHECK_NAME, Status.FAIL,
                           "handshake ok but zero tools exposed", {"tools": names})
    return CheckResult("C2", _c2.CHECK_NAME, Status.PASS,
                       f"MCP handshake ok, {len(names)} tool(s)", {"tools": names})


async def _c3(ctx: RunContext) -> CheckResult:
    declared = ctx.claims.get("tools")
    actual = ctx.state.get("actual_tools")
    if not declared:
        return CheckResult("C3", _c3.CHECK_NAME, Status.SKIP,
                           "no declared tool list supplied")
    if actual is None:
        return CheckResult("C3", _c3.CHECK_NAME, Status.SKIP, "C2 did not produce a tool list")
    missing = sorted(set(declared) - set(actual))
    extra = sorted(set(actual) - set(declared))
    ev = {"declared": declared, "actual": actual, "missing": missing, "undeclared": extra}
    if missing:
        return CheckResult("C3", _c3.CHECK_NAME, Status.FAIL,
                           f"declared tool(s) not exposed: {', '.join(missing)}", ev)
    note = f"; {len(extra)} undeclared extra" if extra else ""
    return CheckResult("C3", _c3.CHECK_NAME, Status.PASS,
                       f"all {len(declared)} declared tools exposed{note}", ev)


async def _c4(ctx: RunContext) -> CheckResult:
    def _done(status: Status, summary: str, ev: dict | None = None) -> CheckResult:
        res = CheckResult("C4", _c4.CHECK_NAME, status, summary, ev or {})
        ctx.state["c4_result"] = res
        return res

    if not ctx.paid_tool:
        return _done(Status.SKIP,
                     "no paid tool named and none could be inferred — nothing to paywall-check")

    resp = await call_tool_raw(ctx, ctx.paid_tool, ctx.claims.get("sample_args", {}))
    ev = {"status_code": resp.status_code, "body_excerpt": excerpt(resp.text)}

    # case (c): the tool answered without demanding payment at all — likely free,
    # not broken. Distinguish this from a genuinely dead/misconfigured target.
    if resp.status_code == 200:
        return _done(
            Status.WARN,
            "this service appears to be free — no payment required "
            f"(unpaid call to {ctx.paid_tool!r} returned 200)", ev)

    # case (b): anything else non-402 means the target itself is unreachable or
    # broken, not merely unpaywalled. Surface the real HTTP status, and flag the
    # Render "no live backend bound to this host" signal when present.
    if resp.status_code != 402:
        no_server = "no-server" in resp.headers.get("x-render-routing", "").lower()
        note = " — host reports no-server (no live backend bound to this URL)" if no_server else ""
        return _done(
            Status.FAIL,
            f"target unreachable: unpaid call returned HTTP {resp.status_code}, "
            f"expected 402{note}", ev)

    try:
        challenge: PaymentRequiredV1 = parse_challenge(resp.json())
    except Exception as e:
        return _done(Status.FAIL, f"402 body is not a valid x402 v1 challenge: {e}", ev)
    if not challenge.accepts:
        return _done(Status.FAIL, "challenge has empty accepts[]", ev)
    req = challenge.accepts[0]
    ctx.state["requirement"] = req
    ev.update({"scheme": req.scheme, "network": req.network,
               "amount_units": req.max_amount_required, "pay_to": req.pay_to,
               "asset": req.asset})
    return _done(Status.PASS, f"well-formed 402: {req.scheme} on {req.network}", ev)


def _no_challenge_reason(ctx: RunContext) -> str:
    """Derive why C5/C6 have nothing to work with, from C4's actual result."""
    c4 = ctx.state.get("c4_result")
    return c4.summary if c4 is not None else "no challenge captured in C4"


async def _c5(ctx: RunContext) -> CheckResult:
    req = ctx.state.get("requirement")
    if req is None:
        return CheckResult("C5", _c5.CHECK_NAME, Status.SKIP,
                           f"no challenge to evaluate — {_no_challenge_reason(ctx)}")
    if ctx.declared_price is None:
        return CheckResult("C5", _c5.CHECK_NAME, Status.SKIP, "no declared price supplied")
    quoted = units_to_usdt(req.max_amount_required)
    ev = {"declared_usdt": ctx.declared_price, "quoted_usdt": quoted}
    if abs(quoted - ctx.declared_price) > 1e-9:
        return CheckResult(
            "C5", _c5.CHECK_NAME, Status.FAIL,
            f"price mismatch: listing says {ctx.declared_price} but 402 quotes {quoted} "
            "— buyers will be overcharged or calls will fail", ev)
    return CheckResult("C5", _c5.CHECK_NAME, Status.PASS,
                       f"quoted price matches declared ({quoted})", ev)


async def _c6(ctx: RunContext) -> CheckResult:
    req = ctx.state.get("requirement")
    if req is None:
        return CheckResult("C6", _c6.CHECK_NAME, Status.SKIP,
                           f"no challenge to pay — {_no_challenge_reason(ctx)}")
    try:
        signed = ctx.payer.pay(req)
    except PayerRefused as e:
        return CheckResult("C6", _c6.CHECK_NAME, Status.SKIP, f"payment not attempted: {e}",
                           {"policy": str(e)})
    resp = await call_tool_raw(ctx, ctx.paid_tool, ctx.claims.get("sample_args", {}),
                               headers={"X-PAYMENT": signed.header_value}, timeout=20.0)
    ev = {"payer": signed.from_address, "nonce": signed.nonce,
          "amount_usdt": units_to_usdt(signed.amount_units), "network": signed.network,
          "status_code": resp.status_code,
          "settle_header": resp.headers.get("x-payment-response", "")}
    ctx.state["paid_status"] = resp.status_code
    if resp.status_code == 402:
        return CheckResult("C6", _c6.CHECK_NAME, Status.FAIL,
                           "server rejected a validly signed payment (still 402) "
                           "— facilitator verify/settle is broken", ev)
    if resp.status_code != 200:
        return CheckResult("C6", _c6.CHECK_NAME, Status.FAIL,
                           f"paid call returned {resp.status_code}", ev)
    ctx.state["paid_response"] = resp
    ctx.state["spend_usdt"] = ctx.state.get("spend_usdt", 0.0) + ev["amount_usdt"]
    if ev["settle_header"]:
        ctx.state.setdefault("tx_refs", []).append(ev["settle_header"][:120])
    return CheckResult("C6", _c6.CHECK_NAME, Status.PASS,
                       f"payment accepted, settled on {signed.network}", ev)


async def _c7(ctx: RunContext) -> CheckResult:
    resp = ctx.state.get("paid_response")
    if resp is None:
        return CheckResult("C7", _c7.CHECK_NAME, Status.SKIP,
                           "no settled paid call to inspect (see C6)")
    from . import parse_rpc_response
    data = parse_rpc_response(resp)
    ev = {"body_excerpt": excerpt(resp.text)}
    if data is None:
        return CheckResult("C7", _c7.CHECK_NAME, Status.FAIL,
                           "paid response is not parseable JSON-RPC", ev)
    result = data.get("result", {})
    if data.get("error") or result.get("isError"):
        return CheckResult("C7", _c7.CHECK_NAME, Status.FAIL,
                           "paid call settled but tool returned an error", ev)
    content = result.get("content") or []
    texts = [c.get("text", "") for c in content if isinstance(c, dict)]
    if not any(t.strip() for t in texts):
        return CheckResult(
            "C7", _c7.CHECK_NAME, Status.FAIL,
            "PAID BUT EMPTY: settlement succeeded and the tool delivered no content "
            "— this is the worst customer experience possible", ev)
    ev["delivered_chars"] = sum(len(t) for t in texts)
    return CheckResult("C7", _c7.CHECK_NAME, Status.PASS,
                       f"paid delivery contains content ({ev['delivered_chars']} chars)", ev)


async def _c8(ctx: RunContext) -> CheckResult:
    samples: list[float] = []
    async with mcp_client(ctx) as client:
        for _ in range(3):
            t0 = time.perf_counter()
            await client.list_tools()
            samples.append((time.perf_counter() - t0) * 1000)
    p50 = statistics.median(samples)
    ev = {"samples_ms": [round(s, 1) for s in samples], "p50_ms": round(p50, 1)}
    if p50 > 5000:
        return CheckResult("C8", _c8.CHECK_NAME, Status.WARN,
                           f"p50 latency {p50:.0f} ms is slow for agent workflows", ev)
    return CheckResult("C8", _c8.CHECK_NAME, Status.PASS, f"p50 latency {p50:.0f} ms", ev)


async def _c9(ctx: RunContext) -> CheckResult:
    free_tool = None
    for t in ctx.state.get("actual_tools", []):
        if t != ctx.paid_tool:
            free_tool = t
            break
    tool = free_tool or "__preflight_nonexistent__"
    resp = await call_tool_raw(ctx, tool, {"__bogus__": ["not", "expected", 42]}, timeout=6.0)
    ev = {"probed_tool": tool, "status_code": resp.status_code,
          "body_excerpt": excerpt(resp.text, 300)}
    if resp.status_code >= 500:
        return CheckResult("C9", _c9.CHECK_NAME, Status.FAIL,
                           f"malformed input crashed the server ({resp.status_code})", ev)
    t0 = time.perf_counter()
    alive = await ctx.http.get(ctx.target_url, timeout=5.0,
                               headers={"accept": "application/json, text/event-stream"})
    ev["alive_after_ms"] = int((time.perf_counter() - t0) * 1000)
    if alive.status_code >= 500:
        return CheckResult("C9", _c9.CHECK_NAME, Status.FAIL,
                           "server unhealthy after malformed input", ev)
    return CheckResult("C9", _c9.CHECK_NAME, Status.PASS,
                       "malformed input handled gracefully; server healthy", ev)


c1_reachability = _mk(_c1, "C1", "Reachability and transport")
c2_handshake = _mk(_c2, "C2", "MCP handshake and tool list")
c3_schema = _mk(_c3, "C3", "Declared tools vs exposed tools")
c4_challenge = _mk(_c4, "C4", "x402 paywall challenge validity")
c5_price = _mk(_c5, "C5", "Price integrity (quote vs listing)")
c6_settlement = _mk(_c6, "C6", "Signed payment accepted and settled")
c7_delivery = _mk(_c7, "C7", "Paid delivery has real content")
c8_latency = _mk(_c8, "C8", "Latency sample (p50 of 3)")
c9_malformed = _mk(_c9, "C9", "Malformed-input resilience")

ALL_CHECKS = [c1_reachability, c2_handshake, c3_schema, c4_challenge, c5_price,
              c6_settlement, c7_delivery, c8_latency, c9_malformed]

CHECK_BUDGETS_S = {"C1": 8, "C2": 10, "C3": 2, "C4": 8, "C5": 2,
                   "C6": 25, "C7": 4, "C8": 12, "C9": 10}
