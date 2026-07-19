"""Check suite: nine small, independent probes sharing one RunContext.

Each check is `async def run(ctx) -> CheckResult` and must never raise —
failures are results, not exceptions. Order matters only where a later check
consumes evidence produced earlier (C4 challenge -> C5/C6; C6 response -> C7).
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..models import CheckResult, Status
from ..payer import Payer

JSONRPC_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


@dataclass
class RunContext:
    target_url: str
    claims: dict[str, Any]
    payer: Payer
    http: httpx.AsyncClient
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def paid_tool(self) -> str | None:
        return self.claims.get("paid_tool")

    @property
    def declared_price(self) -> float | None:
        p = self.claims.get("price_usdt")
        return float(p) if p is not None else None


def timed(fn):
    async def wrapper(ctx: RunContext) -> CheckResult:
        t0 = time.perf_counter()
        try:
            res: CheckResult = await fn(ctx)
        except Exception as e:  # a check must yield a result, never explode
            res = CheckResult(fn.CHECK_ID, fn.CHECK_NAME, Status.FAIL,
                              f"check crashed: {type(e).__name__}: {e}",
                              {"error": f"{type(e).__name__}: {e}"})
        res.duration_ms = int((time.perf_counter() - t0) * 1000)
        return res
    wrapper.CHECK_ID, wrapper.CHECK_NAME = fn.CHECK_ID, fn.CHECK_NAME
    return wrapper


def rpc_body(method: str, params: dict | None = None, id_: int = 1) -> dict:
    b: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        b["params"] = params
    return b


def parse_rpc_response(resp: httpx.Response) -> dict | None:
    """Handle both plain-JSON and SSE-framed streamable-http responses."""
    ctype = resp.headers.get("content-type", "")
    text = resp.text
    if "text/event-stream" in ctype:
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


async def call_tool_raw(ctx: RunContext, tool: str, args: dict,
                        headers: dict | None = None, timeout: float = 8.0) -> httpx.Response:
    h = dict(JSONRPC_HEADERS)
    if headers:
        h.update(headers)
    return await ctx.http.post(
        ctx.target_url,
        json=rpc_body("tools/call", {"name": tool, "arguments": args}),
        headers=h,
        timeout=timeout,
    )


@asynccontextmanager
async def mcp_client(ctx: RunContext, timeout: float = 8.0):
    from fastmcp import Client
    client = Client(ctx.target_url, timeout=timeout)
    async with client:
        yield client


def excerpt(data: Any, limit: int = 800) -> str:
    s = data if isinstance(data, str) else json.dumps(data, default=str)
    return s[:limit] + ("…" if len(s) > limit else "")
