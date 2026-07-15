# ASP PreFlight — MVP Architecture

Status: LOCKED (Phase 3). Scope source: MVP MoSCoW (Must-list only).
Prime directive: simplest design that makes the golden path bulletproof.

## 1. System overview

One deployable service ("preflight") + one demo fixture ("broken-bazaar"). No other components.

```
Builder's agent (Claude Code / okx.ai chat / any MCP client)
        │  MCP call: preflight_run(...)  — paid 1 USDT via x402 (seller middleware)
        ▼
┌─────────────────────────────────────────────────────────┐
│ preflight  (one container, one Python process)          │
│  FastAPI app                                            │
│   ├─ /mcp            FastMCP mount, wrapped by OKX      │
│   │                  Payment SDK middleware (seller)    │
│   ├─ /report/{id}    server-rendered scorecard (Jinja2) │
│   ├─ /verify/{id}    badge resolver (SHOULD tier)       │
│   └─ /healthz                                           │
│  runner.py    orchestrates checks C1–C9, budgets,       │
│               evidence capture                          │
│  payer.py     buyer-side x402 client, own EOA wallet,   │
│               spend caps, idempotency ledger            │
│  store.py     SQLite (one table)                        │
└──────┬──────────────────────────────┬───────────────────┘
       │ paid + unpaid test calls     │ settlement / receipts
       ▼                              ▼
  Target ASP (customer's MCP,     X Layer (USDT transfer,
  or broken-bazaar in the demo)   tx hash evidence)
```

## 2. Components (each with one purpose)

| Component | Purpose | Explicitly NOT its job |
|---|---|---|
| `app.py` | Route wiring: mounts FastMCP, report pages, health | No business logic |
| `runner.py` | Execute check list sequentially, enforce budgets, assemble report | No network specifics |
| `checks/c1..c9.py` | One pure module per check; common `CheckResult` dataclass | No storage, no payments |
| `payer.py` | x402 buyer cycle: fetch 402 quote → validate → sign USDT transfer from EOA → settle → return receipt | No decisions about *whether* to pay (runner decides) |
| `store.py` | Insert/fetch report rows in SQLite | No rendering |
| `report_html.py` | Jinja2 template → scorecard page; one static CSS | No JS framework, no build step |
| `fixtures/broken_bazaar.py` | Separate tiny FastMCP app with `BUG_PRICE` / `BUG_EMPTY` env toggles | Never imported by preflight at runtime |

`CheckResult = {id, name, status: pass|fail|warn|skip, evidence: dict, duration_ms}`.

## 3. Request sequence (golden path)

1. Seller middleware validates/settles the caller's 1 USDT x402 payment (SDK-owned; we do not touch it).
2. `preflight_run(target_mcp_url, declared_tools?, declared_price_usdt?)` invoked.
3. SSRF guard resolves + validates target host (see §7). Fail → structured error, no charge beyond SDK behavior.
4. Runner executes C1→C9 with per-check timeboxes (§5). C6 asks `payer` for exactly one settled purchase (cap-checked).
5. Report row written to SQLite; slug id generated (10-char base32).
6. Tool returns: markdown summary table + overall PASS/FAIL + `https://<domain>/report/{id}`.

Total wall-clock budget: 75 s hard (MCP-call safe). Any check exceeding its box → status `fail` with `evidence.timeout=true`; run continues.

## 4. Data model (SQLite, single table)

```
reports(
  id TEXT PRIMARY KEY,          -- slug
  created_at TEXT,              -- ISO8601 UTC
  target_url TEXT,
  claims_json TEXT,             -- declared tools/price as given
  results_json TEXT,            -- list[CheckResult]
  overall_status TEXT,          -- PASS | FAIL
  spend_usdt REAL,
  tx_hashes_json TEXT
)
```
Plus `payments(intent_id, run_id, amount, status, tx_hash, created_at)` as the payer's idempotency ledger (written BEFORE signing; a retried run reuses a settled intent instead of re-paying).

## 5. Check suite budgets (sum 62 s < 75 s budget)

| Check | Timebox | Evidence captured |
|---|---|---|
| C1 reachability/TLS/latency | 6 s | status, cert CN, ms |
| C2 MCP handshake + tools/list | 8 s | tool names |
| C3 schema fidelity vs claims | 2 s (local) | diff |
| C4 402 challenge validity | 6 s | raw 402 payload excerpt |
| C5 price integrity | 1 s (local) | quoted vs declared |
| C6 real settlement | 20 s | tx hash + explorer URL |
| C7 paid delivery + shape | 8 s | response excerpt (truncated 2 KB) |
| C8 latency p50 (3 calls) | 8 s | 3 samples |
| C9 malformed-input probe (1) | 3 s | error behavior |

Degraded mode (if buyer settlement blocked at go/no-go): C6 becomes `skip` with reason; C7 runs against free/dev path if target provides one, else `skip`. Product remains sellable as protocol-validation suite.

## 6. Payments

Seller side (revenue): OKX Payment SDK middleware wrapping `/mcp`; price 1 USDT per `preflight_run`. Settlement asset/network per platform defaults (USDT on X Layer).

Buyer side (the tested purchase): plain EOA keypair, private key in `PAYER_PRIVATE_KEY` env, funded ≤ 20 USDT (blast-radius cap) + small OKB buffer despite zero-gas claims. Rules enforced in `payer.py`:
- refuse any 402 quote > `MAX_PAY_PER_CALL_USDT` (default 2)
- daily ceiling `MAX_PAY_PER_DAY_USDT` (default 10)
- `PAYER_KILL_SWITCH=1` → C6/C7 auto-skip
- intent row before signature (idempotency)

## 7. Security & reliability floor (MUST)

- SSRF guard: https-only targets; resolve DNS, reject private/loopback/link-local/metadata ranges (10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, ::1, fc00::/7); re-validate on any redirect; no redirect following in paid calls.
- Timeouts on every outbound call; no unbounded reads (2 KB evidence truncation).
- Structured JSON logs to stdout; secrets redacted by key-name filter.
- Idempotent runs; safe re-entry after crash (payment ledger).
- Rate limit: in-memory token bucket per caller ID for the free tier (SHOULD); paid tier is naturally gated by payment.

## 8. Deployment

- Platform: Fly.io or Railway (decision at build start based on 10-minute TLS+deploy spike; both acceptable). One container for `preflight`, one for `broken-bazaar`.
- Domains: `api.<name>.<tld>` (MCP + reports), `bazaar.<name>.<tld>` (fixture). Platform-managed certs.
- Config via env only. `Dockerfile` per app; no compose orchestration needed in prod.
- Rollback = redeploy previous image tag.

## 9. Unknowns to pin in HOUR 1 (before feature code)

1. X Layer chain id, USDT contract address, public RPC endpoint (official docs).
2. OKX Payment SDK Python: exact middleware API + which side it settles on (verify against okx/payments repo).
3. FastMCP streamable-HTTP mount alongside FastAPI routes in one ASGI app (10-line spike).
4. Buyer-side x402 flow against one live cheap ASP (THE go/no-go gate).
5. ASP registration prompt flow: fields required for A2MCP listing (dry-run in agent).

## 10. Non-goals (locked)

No accounts/dashboard, no DB server, no queue, no monitoring product features, no A2A targets, no non-MCP HTTP targets, no public grading of non-customers, no output-quality scoring beyond the labeled advisory check (SHOULD tier).
