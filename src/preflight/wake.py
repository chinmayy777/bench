"""Wake phase: pre-warm each candidate's endpoint before probing/purchasing.

Cold-started ASPs (serverless, scale-to-zero hosts) can take many seconds to
answer their first request. Bench sends a throwaway GET to /healthz for each
target, in parallel, before the real probe/purchase phase runs — so a slow
first response doesn't get mistaken for paid latency and doesn't sink the
whole comparison. Any HTTP response at all (even a 404) counts as awake; only
a dead connection/timeout gets retried until the per-target budget runs out.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

_INITIAL_BACKOFF_S = 0.5
_MAX_BACKOFF_S = 5.0
_COLD_THRESHOLD_MS = 2000


@dataclass
class WakeResult:
    woke_ok: bool  # got any HTTP response within the budget
    wake_ms: int | None  # time to first response, if any
    woke: bool  # True if the response took long enough to imply a cold start
    reason: str | None = None  # plain-language reason, set only when woke_ok is False


def _healthz_url(target_url: str) -> str:
    p = urlparse(target_url)
    return f"{p.scheme}://{p.netloc}/healthz"


async def wake_target(target_url: str, timeout_s: float) -> WakeResult:
    url = _healthz_url(target_url)
    t0 = time.perf_counter()
    deadline = t0 + timeout_s
    backoff = _INITIAL_BACKOFF_S
    async with httpx.AsyncClient() as http:
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            try:
                await http.get(url, timeout=min(remaining, 10.0))
            except (httpx.TimeoutException, httpx.TransportError):
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(backoff, remaining))
                backoff = min(backoff * 2, _MAX_BACKOFF_S)
                continue
            wake_ms = int((time.perf_counter() - t0) * 1000)
            return WakeResult(True, wake_ms, wake_ms > _COLD_THRESHOLD_MS)
    return WakeResult(
        False, None, False,
        reason=f"no response within {timeout_s:g}s — service may be down or cold-starting too slowly",
    )


async def wake_targets(targets: list[str], timeout_s: float) -> dict[str, WakeResult]:
    results = await asyncio.gather(*(wake_target(u, timeout_s) for u in targets))
    return dict(zip(targets, results))
