# DEMO_SCRIPT.md — ≤90 seconds

One idea per beat. Screen-record the terminal + the report page. No cuts that
hide a real step; the whole point is that it's real.

**Hook (0:00–0:10)**
> "Every paid agent on OKX faces the same 24-hour review — and one broken
> paywall can invalidate your whole submission. PreFlight test-purchases your
> service like a real buyer, before that happens."

**Beat 1 — the broken service (0:10–0:30)**
- Show BrokenBazaar listed with price 0.05.
- Call `preflight_run` against it (bug: price mismatch).
- Report page renders **FAIL**, big red stamp. Point at **C5**: listing says
  0.05, the 402 quotes 0.25. Expand the evidence — the raw quote is right there.

**Beat 2 — the empty-delivery case (0:30–0:45)**
- Flip to the empty-bug instance. Run again.
- **C6 passes** (payment settles) but **C7 fails**: "PAID BUT EMPTY."
> "This is the worst case — the customer pays and gets nothing. PreFlight caught
> it; review might not."

**Beat 3 — fix & go green (0:45–1:05)**
- Toggle the bugs off (env flip / redeploy).
- Run once more. Report flips to **PASS** — all nine green, including a real
  settlement reference on C6.
> "Same deterministic checks, same input, same verdict. Now it's safe to list."

**Beat 4 — self-test (1:05–1:20)**
- Run PreFlight against *its own* endpoint. Green report.
> "It even preflights itself — that green report is linked in our listing."

**Close (1:20–1:30)**
> "PreFlight. CI for paid agents, live on OKX.AI. Free to run — go check your
> own service before review does." Show listing URL + #OKXAI.

Assets ready in `/mnt/user-data/outputs`: `sample_report_PASS.html`,
`sample_report_FAIL.html` (open in a browser for clean screen-grabs if a live
capture is risky).
