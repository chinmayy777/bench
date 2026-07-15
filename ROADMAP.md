# Roadmap — ASP PreFlight (locked MVP)

Hard external deadline: submissions close Jul 17, 23:59 UTC (X post + Google Form).
Internal listing deadline: **Jul 16, 18:00 UTC** — ASP submitted for review with full retry buffer (review SLA ≤24 h).
All times UTC. Blocks assume solo build.

## Day 1 — Jul 15 (remaining ~10 h)

| Block | Task | Exit criterion |
|---|---|---|
| H1 (1 h) | Pin unknowns: X Layer chain id / USDT contract / RPC; Payment SDK Python surface; FastMCP+FastAPI mount spike; deploy-platform 10-min spike | All five answers written into ARCHITECTURE.md §9 |
| H2–H3 (2–4 h) | **GO/NO-GO GATE:** settle ONE buyer-side x402 payment from our EOA against a live cheap ASP; capture tx hash | Settled tx on X Layer explorer. If blocked >4 h → degraded mode decision (C6 skip) and continue; ChainCanvas fallback trigger only if listing itself is impossible |
| H4–H8 (5 h) | Check suite C1–C9 + CheckResult + runner budgets + evidence capture | Suite runs green against a known-good target locally |
| H9–H10 (2 h) | BrokenBazaar fixture (BUG_PRICE, BUG_EMPTY) deployed to its subdomain | Suite correctly fails C5 / C7 against each bug state |

## Day 2 — Jul 16 (to 18:00)

| Block | Task | Exit criterion |
|---|---|---|
| H1–H3 (3 h) | Our paid MCP endpoint: FastMCP tool + Payment SDK middleware + deploy `preflight` container + domain TLS | Paid `preflight_run` callable end-to-end from Claude Code with real 1 USDT payment |
| H4–H5 (2 h) | SQLite store + report permalink page (polished, mobile-readable) | Report URL renders scorecard with evidence expanders |
| H6 (1 h) | Hardening pass: SSRF guard, timeouts, caps, kill switch, 5 consecutive clean golden-path runs incl. both fixture states | 5/5 green; logs clean |
| H7 (1 h) | Self-test ceremony: PreFlight report on PreFlight; link saved for listing | Green self-report permalink |
| H8 (1 h) | ASP registration + listing package (final name, one-liner, description, icon, 1 USDT price) → **SUBMIT FOR REVIEW ≤18:00** | Confirmation of submission |
| H9–H10 (2 h) | Demo video ≤90 s (script: broken → red → fix → green → badge/self-test) + X post draft #OKXAI | Final cut exported |

SHOULD-tier (only if all above green by Jul 16 12:00): badge PNG + /verify route → free `preflight_lite` → re-run diff → "first 10 builders free" GTM post in hackathon channels.

## Day 3 — Jul 17 (buffer)

- React to review outcome (fix + resubmit if needed — this buffer is the whole point of the internal deadline).
- Publish X post; submit Google Form well before 23:59 UTC.
- If listed early: GTM push to fellow entrants; accumulate orders/reviews (Revenue Rocket signal).

## Standing rules
- Any task overrunning its box by >50% → cut scope from SHOULD list first, never from reliability floor.
- No new dependencies after Day 1 H8 without a written decision entry.
- Definition of demo-done: golden path 5/5 clean on both fixture states; self-test green; 90-s cut needs zero editing tricks.
