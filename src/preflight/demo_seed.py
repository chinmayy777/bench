"""Permanent demo comparison seed.

Storage is ephemeral on free hosting, so real runs vanish on redeploy. This
seeds a stable /compare/demo from a representative real run (with genuine
on-chain tx hashes) so the landing button and the marketplace listing always
resolve to a live-looking comparison.

Idempotent: only writes if `demo` is missing.
"""
from __future__ import annotations

import logging

from .bench import Candidate, Comparison, _rank
from .store import load_comparison, save_comparison

log = logging.getLogger("bench.seed")

DEMO_ID = "demo"

# Representative metrics from a real decisive run; tx hashes are genuine
# Base Sepolia settlements so the evidence links resolve on the explorer.
_DEMO_ROWS = [
    ("https://vendor-mid.onrender.com/mcp/", 0.05, 2700, 441,
     "base-sepolia:0x2e0ecc0514c90b0c20bcaec756e0f04de55c5956f73833d76ea4e2e42cb6b274"),
    ("https://vendor-cheap.onrender.com/mcp/", 0.02, 3382, 209,
     "base-sepolia:0xf205c55302d89b6dc43b236c9013c0ad73589ad79b62b9ca06c75b33cd8e64b5"),
    ("https://vendor-rich.onrender.com/mcp/", 0.15, 3666, 503,
     "base-sepolia:0x59ee76c56f6d2485538bba37b96b0dadf3da96423825170d4f74368a864600ff"),
]


def ensure_demo_comparison() -> None:
    """Write the demo comparison if it isn't already present."""
    try:
        if load_comparison(DEMO_ID) is not None:
            return
    except Exception:
        pass  # table may not exist yet; save will create it

    cands = [
        Candidate(target_url=url, reachable=True, purchased=True,
                  price_usdt=price, latency_ms=lat, delivered_chars=chars,
                  tx_ref=tx, report_id=f"demo-{i}", notes=[])
        for i, (url, price, lat, chars, tx) in enumerate(_DEMO_ROWS)
    ]
    _rank(cands)  # fills value_score + honest verdicts
    cands.sort(key=lambda c: (c.usable, c.value_score), reverse=True)
    winner = cands[0].target_url if cands and cands[0].usable else None

    comp = Comparison(
        id=DEMO_ID, created_at="2026-07-16T13:00:00Z", task="market pulse feed",
        candidates=cands, winner_url=winner,
        total_spend_usdt=round(sum(c.price_usdt or 0 for c in cands), 6),
        tx_refs=[c.tx_ref for c in cands if c.tx_ref],
    )
    save_comparison(comp)
    log.info("seeded permanent demo comparison at /compare/%s", DEMO_ID)
