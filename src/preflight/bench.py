"""Bench — buyer-side comparison shopper for paid agent services.

Given several ASP endpoints that claim to do the same job, Bench probes each,
optionally makes ONE real x402 purchase from each, and ranks them on a
transparent value score built from price, latency, and delivery completeness.

Reuses the single-target check engine (runner.run_preflight) per candidate,
then adds the cross-candidate ranking that is Bench's contribution.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .models import Report, Status, now_iso
from .payer import Payer
from .runner import run_preflight
from .ssrf import TargetRejected
from .store import new_report_id, save_comparison

log = logging.getLogger("bench")

# Weights for the value score (sum 1.0). Lower price/latency better; higher
# delivery completeness better. Tunable, but transparent by design.
W_PRICE = 0.45
W_LATENCY = 0.25
W_DELIVERY = 0.30


@dataclass
class Candidate:
    """One ASP's measured result within a comparison."""
    target_url: str
    reachable: bool
    purchased: bool
    price_usdt: float | None
    latency_ms: int | None
    delivered_chars: int | None
    tx_ref: str | None
    report_id: str
    notes: list[str] = field(default_factory=list)
    value_score: float = 0.0  # 0..100, filled by ranking

    @property
    def usable(self) -> bool:
        return self.reachable and self.purchased and (self.delivered_chars or 0) > 0


@dataclass
class Comparison:
    id: str
    created_at: str
    task: str
    candidates: list[Candidate]
    winner_url: str | None
    total_spend_usdt: float
    tx_refs: list[str]


def _extract(report: Report) -> dict[str, Any]:
    """Pull the comparable metrics out of a single-target report."""
    by_id = {r.id: r for r in report.results}
    price = None
    if (c5 := by_id.get("C5")) and c5.evidence.get("quoted_usdt") is not None:
        price = float(c5.evidence["quoted_usdt"])
    elif (c4 := by_id.get("C4")) and c4.evidence.get("amount_units"):
        try:
            price = int(c4.evidence["amount_units"]) / 1_000_000
        except (TypeError, ValueError):
            price = None
    latency = None
    if (c6 := by_id.get("C6")) and c6.status == Status.PASS and c6.duration_ms:
        latency = int(c6.duration_ms)  # real paid-delivery latency — what a buyer feels
    elif (c8 := by_id.get("C8")) and c8.evidence.get("p50_ms") is not None:
        latency = int(c8.evidence["p50_ms"])
    elif (c1 := by_id.get("C1")) and c1.evidence.get("first_byte_ms") is not None:
        latency = int(c1.evidence["first_byte_ms"])
    delivered = None
    if (c7 := by_id.get("C7")) and c7.evidence.get("delivered_chars") is not None:
        delivered = int(c7.evidence["delivered_chars"])
    purchased = by_id.get("C6", None) is not None and by_id["C6"].status == Status.PASS
    reachable = by_id.get("C1", None) is not None and by_id["C1"].status == Status.PASS
    return {"price": price, "latency": latency, "delivered": delivered,
            "purchased": purchased, "reachable": reachable}


def _rank(candidates: list[Candidate]) -> None:
    """Assign 0..100 value scores. Normalizes each axis across usable candidates."""
    usable = [c for c in candidates if c.usable]
    if not usable:
        return

    prices = [c.price_usdt for c in usable if c.price_usdt is not None]
    lats = [c.latency_ms for c in usable if c.latency_ms is not None]
    delivs = [c.delivered_chars for c in usable if c.delivered_chars is not None]

    def norm(v, lo, hi, invert=False):
        if v is None or hi == lo:
            return 0.5
        x = (v - lo) / (hi - lo)
        return 1 - x if invert else x

    p_lo, p_hi = (min(prices), max(prices)) if prices else (0, 1)
    l_lo, l_hi = (min(lats), max(lats)) if lats else (0, 1)
    d_lo, d_hi = (min(delivs), max(delivs)) if delivs else (0, 1)

    for c in usable:
        s = (W_PRICE * norm(c.price_usdt, p_lo, p_hi, invert=True)
             + W_LATENCY * norm(c.latency_ms, l_lo, l_hi, invert=True)
             + W_DELIVERY * norm(c.delivered_chars, d_lo, d_hi))
        c.value_score = round(s * 100, 1)


async def compare_services(
    targets: list[str],
    paid_tool: str | None = None,
    price_usdt: float | None = None,
    sample_args: dict | None = None,
    task: str = "",
    payer: Payer | None = None,
) -> Comparison:
    """Probe + buy from each target concurrently, then rank by value."""
    if not targets or len(targets) < 2:
        raise ValueError("compare needs at least 2 target URLs")
    if len(targets) > 5:
        raise ValueError("compare supports at most 5 targets per run")

    payer = payer or Payer()
    claims = {k: v for k, v in {
        "paid_tool": paid_tool, "price_usdt": price_usdt,
        "sample_args": sample_args or {},
    }.items() if v is not None}

    async def probe(url: str) -> Candidate:
        try:
            report = await run_preflight(url, dict(claims), payer=payer)
        except TargetRejected as e:
            return Candidate(url, False, False, None, None, None, None,
                             report_id="", notes=[f"rejected: {e}"])
        m = _extract(report)
        notes = []
        if not m["reachable"]:
            notes.append("unreachable")
        if not m["purchased"]:
            notes.append("purchase failed or skipped")
        if m["delivered"] is not None and m["delivered"] == 0:
            notes.append("paid but empty")
        return Candidate(
            target_url=url, reachable=m["reachable"], purchased=m["purchased"],
            price_usdt=m["price"], latency_ms=m["latency"],
            delivered_chars=m["delivered"], tx_ref=(report.tx_refs[0] if report.tx_refs else None),
            report_id=report.id, notes=notes,
        )

    candidates = await asyncio.gather(*(probe(u) for u in targets))
    candidates = list(candidates)
    _rank(candidates)
    candidates.sort(key=lambda c: (c.usable, c.value_score), reverse=True)

    winner = candidates[0].target_url if candidates and candidates[0].usable else None
    total_spend = round(sum(c.price_usdt or 0 for c in candidates if c.purchased), 6)
    tx_refs = [c.tx_ref for c in candidates if c.tx_ref]

    comp = Comparison(
        id=new_report_id(), created_at=now_iso(), task=task or (paid_tool or "comparison"),
        candidates=candidates, winner_url=winner,
        total_spend_usdt=total_spend, tx_refs=tx_refs,
    )
    save_comparison(comp)
    return comp


def comparison_markdown(comp: Comparison, base_url: str) -> str:
    lines = [f"# Bench comparison — {comp.task}", ""]
    if comp.winner_url:
        lines.append(f"**Best value: {comp.winner_url}**\n")
    else:
        lines.append("**No usable service among the candidates.**\n")
    lines.append("| Service | Score | Price | Latency | Delivered | Status |")
    lines.append("|---|---|---|---|---|---|")
    for c in comp.candidates:
        price = f"{c.price_usdt}" if c.price_usdt is not None else "—"
        lat = f"{c.latency_ms}ms" if c.latency_ms is not None else "—"
        deliv = f"{c.delivered_chars}c" if c.delivered_chars is not None else "—"
        status = "✅ usable" if c.usable else ("⚠️ " + ", ".join(c.notes) if c.notes else "—")
        score = f"{c.value_score}" if c.usable else "—"
        lines.append(f"| {c.target_url} | {score} | {price} | {lat} | {deliv} | {status} |")
    if comp.total_spend_usdt:
        lines.append(f"\nTotal test spend: {comp.total_spend_usdt} (non-mainnet)")
    lines.append(f"\nFull comparison: {base_url}/compare/{comp.id}")
    return "\n".join(lines)
