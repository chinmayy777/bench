# PROJECT — ASP PreFlight

One-liner: **PreFlight test-purchases your paid ASP like a real buyer-agent and tells you — with on-chain evidence — whether your paywall, delivery, and claims actually work, before review or a customer finds out.**

## What it is
A paid A2MCP service on OKX.AI. A builder points it at their MCP endpoint + declared claims; PreFlight runs a 9-point commerce-grade check suite — including one REAL x402 purchase from its own funded wallet — and returns a pass/fail scorecard with evidence (raw 402 payload, X Layer tx hash, latencies) at a shareable permalink.

## Who buys it, and why now
Every ASP builder facing OKX's binary 24-hour listing review (135+ hackathon entrants this week; the platform's permanent onboarding funnel after). A failed review can invalidate an entire hackathon submission; a broken paywall after listing burns real paying buyers. 1 USDT to de-risk both.

## Why it can win
- Judges' stated signal is real problems + real usage; PreFlight's customers are provably present DURING the judging window and reachable via hackathon channels → the only realistic Revenue Rocket path, plus Best Product / Software Utility / Creative Genius surfaces.
- Genuine autonomy: an agent that spends real money and renders judgment, with on-chain receipts.
- Maximal ecosystem showcase: every run IS an OKX.AI x402 transaction; it demonstrates agent-pays-agent commerce live.
- Consent-based by design (sellers hire it on their own service) → high approval probability; it helps the reviewers' funnel rather than auditing it.
- Portable thesis beyond the hackathon: billing-flow correctness testing for the whole paid-MCP/x402 economy ("CI for paid agents").

## Deliverables checklist (hackathon)
- [ ] ASP listed + live on OKX.AI (internal target: submitted for review Jul 16 18:00 UTC)
- [ ] Self-test: PreFlight's green report on PreFlight, linked in listing
- [ ] ≤90 s demo (broken → red → fix → green)
- [ ] X post with #OKXAI
- [ ] Google Form before Jul 17 23:59 UTC

## Guardrails
Working title pending name check. Scope frozen to MUST list (see ARCHITECTURE.md); SHOULD items only after 5/5 clean golden-path runs. Fallback (ChainCanvas) exists solely for a listing-impossible blocker; otherwise no idea churn.
