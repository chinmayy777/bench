# Pivot Decision — PreFlight → Bench

## Why the pivot (D10)
During pre-listing name/collision check, discovered "Pre-Flight Safety Scanner"
already built AND submitted to THIS hackathon by another team
(okx-genesis-pre-flight.vercel.app), with near-identical positioning: A2MCP
trust scanner, "live mystery shopper" on-chain, bait-and-switch price detection,
EIP-712 signed reports. Listing our PreFlight would make us the visibly-second
near-identical entry in an originality-judged competition — worst position.
General-web prior art also dense (AgentGrade, x402station, Vinaystwt PreFlight).

## The pivot (Path 1: reuse the engine, change the target)
Kept ~80% of the codebase — the "probe an MCP endpoint, pay via x402, capture
evidence, return a report" engine — and changed WHAT we probe and WHY:
from seller-side "grade one ASP's paywall" to buyer-side "compare several ASPs
and rank by value." Different customer (buyers, not sellers), different job
(choose, not pre-list), no collision with the scanner/gate crowd.

## Why Bench specifically
- Reuses the hardest-won code: the real on-chain settlement path (proven on
  BaseScan). Each candidate gets one real settled purchase.
- Serves the buyer side that scanners and release-gates don't.
- Answers the price-discovery gap the x402 ecosystem repeatedly names as unsolved.
- Demo is strong autonomous-AI theatre: an agent buys from 3 services and picks
  the best — showcases OKX.AI's marketplace working end to end.

## What changed vs kept
KEPT: MCP client, check runner + suite, x402kit, payer + cash-free guardrails,
DirectSettleFacilitator (real base-sepolia settlement), SSRF, store pattern,
report page styling.
NEW: bench.py (compare orchestration + value ranking), compare_services MCP
tool, comparison.html leaderboard, vendor_sim.py (configurable comparable
vendors), comparisons table in store, 4 Bench tests.

## Value score (Bench's IP)
price 45% · latency 25% · delivery 30%, each normalized across usable
candidates. Non-usable (unreachable / purchase-failed / paid-but-empty)
excluded from winning. Transparent and tunable by design.
