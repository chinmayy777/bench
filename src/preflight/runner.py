"""Run the suite against one target with hard time budgets; assemble the report."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .checks import RunContext
from .checks.suite import ALL_CHECKS, CHECK_BUDGETS_S
from .config import settings
from .models import CheckResult, Report, Status, now_iso
from .payer import Payer
from .ssrf import validate_target_url
from .store import new_report_id, save_report

log = logging.getLogger("preflight.runner")


def _compute_overall(results: list[CheckResult]) -> str:
    """PASS is a claim that the run actually completed and actually verified
    what it says it did — never emit it over a check that crashed or over a
    purchase that never happened.

    1. Any check FAILing at all is disqualifying. There is no longer a
       hard-coded gating subset (C8/C9 used to be excluded from it, so a
       crashed latency sampler could silently sit under a green headline —
       exactly the bug this replaces).
    2. C4 reaching PASS means a real, payable 402 challenge existed — the
       one thing this product claims to verify is that such a challenge gets
       paid and delivered. If C6 (the settlement) doesn't also reach PASS —
       whether it FAILed (already caught by rule 1) or was SKIPped (refused
       by payer policy, a cap, missing key, etc.) — the purchase the headline
       would be implicitly vouching for never happened.
    A SKIP elsewhere (C2 on a non-MCP target, C5/C6 with no payable challenge
    at all — i.e. free targets or unsupported networks) is a legitimate
    shape/environment classification, not an incomplete run, and must not by
    itself block PASS.
    """
    if any(r.status == Status.FAIL for r in results):
        return "FAIL"
    by_id = {r.id: r for r in results}
    c4 = by_id.get("C4")
    if c4 is not None and c4.status == Status.PASS:
        c6 = by_id.get("C6")
        if c6 is None or c6.status != Status.PASS:
            return "FAIL"
    return "PASS"


async def run_preflight(target_url: str, claims: dict[str, Any] | None = None,
                        payer: Payer | None = None) -> Report:
    claims = claims or {}
    target_url = validate_target_url(target_url, settings.allow_local_targets)
    payer = payer or Payer()
    results: list[CheckResult] = []
    async with httpx.AsyncClient(follow_redirects=False) as http:
        ctx = RunContext(target_url=target_url, claims=claims, payer=payer, http=http)
        deadline = asyncio.get_event_loop().time() + settings.run_budget_s
        for check in ALL_CHECKS:
            remaining = deadline - asyncio.get_event_loop().time()
            budget = min(CHECK_BUDGETS_S.get(check.CHECK_ID, 8), max(remaining, 0.1))
            try:
                res = await asyncio.wait_for(check(ctx), timeout=budget)
            except asyncio.TimeoutError:
                res = CheckResult(check.CHECK_ID, check.CHECK_NAME, Status.FAIL,
                                  f"timed out after {budget:.0f}s",
                                  {"timeout": True, "budget_s": budget})
            log_fn = log.warning if res.status == Status.FAIL else log.info
            log_fn("check=%s status=%s ms=%s summary=%s", res.id, res.status.value,
                   res.duration_ms, res.summary)
            results.append(res)

    overall = _compute_overall(results)
    report = Report(
        id=new_report_id(), created_at=now_iso(), target_url=target_url,
        claims=claims, results=results, overall=overall,
        spend_usdt=round(ctx.state.get("spend_usdt", 0.0), 6),
        tx_refs=ctx.state.get("tx_refs", []),
    )
    save_report(report)
    return report


def summary_markdown(report: Report, base_url: str) -> str:
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭️"}
    lines = [f"# PreFlight {report.overall} — {report.target_url}", ""]
    for r in report.results:
        lines.append(f"- {icon[r.status.value]} **{r.id} {r.name}** — {r.summary}")
    if report.spend_usdt:
        lines.append(f"\nTest spend: {report.spend_usdt} (non-mainnet only)")
    lines.append(f"\nFull evidence: {base_url}/report/{report.id}")
    return "\n".join(lines)
