# Bench

**Your agent shouldn't buy the first service it finds. Bench buys from all of them and tells you which is best.**

Bench is a buyer-side comparison shopper for paid agent services on
[OKX.AI](https://www.okx.ai/). Point it at several ASPs that claim to do the
same job; Bench makes a **real x402 purchase from each**, measures what actually
came back, and returns a ranked scorecard — price paid, delivery latency, and
delivery completeness — with a transparent value score and on-chain evidence.

Where trust scanners check *one* endpoint's safety, Bench answers the buyer's
real question: **of these services that all claim to do X, which is the best
value right now?**

## How it works

`compare_services(targets, paid_tool, sample_args)`:

1. For each candidate ASP, Bench probes it (reachability, MCP handshake, x402
   challenge) and makes **one real settled purchase**.
2. It captures real price paid, paid-call latency, and delivered content size.
3. It ranks on a transparent value score: **price 45% · latency 25% ·
   delivery 30%**, normalized across the field.
4. It returns a leaderboard with the best-value pick highlighted, per-candidate
   evidence, and settlement tx references, at a shareable permalink.

Services that take payment but deliver nothing, or can't be purchased from, are
flagged **not usable** and can't win — a broken service never wins on price.

## Cash-free settlement

Runs on **mock** (offline, real signature crypto) or **base-sepolia** testnet
(free faucet funds). **Mainnet payment is refused in code.** Bench lists free;
runs on free hosting.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m pytest tests/ -q       # 26 tests
PYTHONPATH=src python scripts/bench_demo.py     # 3 vendors + Bench, live compare
```

## Layout

```
src/preflight/
  bench.py          comparison engine + value ranking  (Bench's core)
  app.py            FastAPI + compare_services tool + comparison page
  runner.py         single-target probe engine (reused per candidate)
  checks/suite.py   the probes each candidate is measured with
  payer.py          buyer-side x402 with cash-free guardrails
  x402kit.py        SDK-canonical sign/verify + offline MockFacilitator
  store.py          SQLite (reports + comparisons)
  templates/        comparison.html (leaderboard), report.html
fixtures/broken_bazaar/
  vendor_sim.py     configurable comparable vendor (PRICE/LATENCY_MS/RICHNESS)
  app.py            original fixture + DirectSettleFacilitator (on-chain settle)
scripts/bench_demo.py   boot 3 vendors + Bench, compare through the MCP surface
tests/                  26 unit + e2e tests
```

Status: engine complete, 26/26 tests green, demo runs through the live MCP
surface. Real Base Sepolia settlement path proven.
