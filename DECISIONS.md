# Decision Log — ASP PreFlight

## D0. Project selection lineage (Phases 1–2, final)
- ChainCanvas selected (empty Artistic Excellence category, 10/10 demo). 
- Red-team under founder-weighted criteria (autonomy, ecosystem, commercial) → switched to TrustLens (autonomous mystery shopper).
- Premortem killed TrustLens: public grading of reviewer-approved ASPs = discretionary listing-rejection risk; rejection invalidates the entire submission (rule: ASP must pass review and go live). Also: no ground truth for quality grades at n≈2.
- Consent-flipped variant survived every attack → ASP PreFlight: seller-initiated pre-listing QA. Ground truth = seller's own declared claims; results private; helps (not audits) OKX review.
- Final blank-slate Top-10 pass confirmed PreFlight #1 (8.35 weighted) over ChainCanvas (7.76). ChainCanvas retained as fully-specified fallback until go/no-go gate passes.
- LOCKED per founder instruction 2026-07-15. No new ideas unless a critical blocker appears.

## D1. A2MCP over A2A
Deterministic request/response, instant x402 settlement, no negotiation ops, no dispute exposure, no online-presence requirement. A2A adds 1+ day of risk for zero demo value in this product.

## D2. Single container over serverless
A full run ≈ 10 outbound calls incl. on-chain settlement wait (~60 s worst case). Edge/function time limits and cold starts threaten exactly the moment we cannot fail (live demo, judge trial). Container gives predictable wall-clock, SQLite disk, managed TLS. Tradeoff accepted: we manage one host.

## D3. Synchronous runs inside the MCP call (no queue)
75 s hard budget with per-check timeboxes fits MCP call tolerances. A queue adds a failure class (stuck jobs) and an infra piece for zero user benefit at MVP volume. Revisit only if runs must exceed ~90 s (Future Work: batch mode).

## D4. SQLite + server-rendered Jinja2 report page
One table, one template, zero build tooling. The permalink page is the entire UI and the demo artifact. Postgres/React deferred indefinitely.

## D5. Buyer wallet = plain EOA (not user Agentic Wallet)
PreFlight buys with ITS OWN pre-funded EOA (≤20 USDT), so TEE/confirmation friction never applies; user funds never touched. Caps: 2 USDT/call, 10 USDT/day, kill switch, pay-intent ledger for idempotency.

## D6. BrokenBazaar is a real separate deployment
The red→green demo must be honest: a genuinely remote, genuinely broken endpoint (env-toggled bugs), not an in-process mock.

## D7. Nine deterministic checks; LLM only in a labeled advisory check (SHOULD tier)
Credibility rests on reproducible, protocol-level assertions. Advisory claim-vs-output rubric is clearly marked non-deterministic and never affects PASS/FAIL.

## D8. Naming
"ASP PreFlight" is a working title ("preflight" is a common dev term; collision check + final name due before listing copy). Shortlist: PreFlight, LaunchCheck, GreenLight.
