# RUNBOOK — deploy, verify, list, demo

Four steps must run outside the build sandbox (it can't reach OKX, RPCs, or
deploy platforms). Do them in order. Target: ASP submitted for review by
**Jul 16 18:00 UTC**, giving a full day of retry buffer before submissions
close Jul 17 23:59 UTC.

---

## Step 1 — Deploy both services (free tier)

Using Render (or any Docker host with a free tier + subdomain):

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, point at the repo. `render.yaml` defines both
   services (`preflight` and `broken-bazaar`).
3. Set env vars per `.env.example`. For `preflight`, set `BASE_URL` to its own
   Render URL (e.g. `https://preflight-xxx.onrender.com`), `PAYER_MODE=mock` for
   now.
4. Wait for both to go green. Confirm:
   - `curl https://<preflight>/healthz` → `{"ok": true, ...}`
   - `curl https://<bazaar>/healthz` (bazaar exposes `/mcp/`; a GET to `/` is fine)

The platform subdomains satisfy OKX's HTTPS + domain requirement — no domain
purchase needed.

> Free tiers sleep when idle. Add a free cron ping (e.g. cron-job.org) hitting
> `/healthz` every 10 min so the judge/demo never hits a cold start.

---

## Step 2 — Prove real testnet settlement (go/no-go)

This is the one step that turns "mock" into "real on-chain," at zero cost.

1. Create a throwaway EOA (never reuse a real wallet). Keep the private key.
2. Get free **Base Sepolia USDC** from Circle's faucet
   (https://faucet.circle.com — select Base Sepolia) into that address. Grab a
   little Base Sepolia ETH for gas from a Base Sepolia faucet too.
3. Redeploy `broken-bazaar` with `NETWORK=base-sepolia`, `FACILITATOR=okx`,
   `PAY_TO=<your throwaway address>`, `RESOURCE_URL=https://<bazaar>/mcp/`.
4. From your machine (repo checked out, deps installed):

   ```bash
   PAYER_MODE=testnet \
   PAYER_PRIVATE_KEY=0x<throwaway key> \
   BAZAAR_URL=https://<bazaar>/mcp/ \
   BASE_URL=https://<preflight> \
   python scripts/verify_testnet.py
   ```

5. Expect `✅ GO` with a settlement reference / tx hash. If `NO-GO`: re-check
   faucet balance and that the fixture really has `FACILITATOR=okx
   NETWORK=base-sepolia`.

If testnet settlement won't cooperate before your deadline, PreFlight still
lists and demos in **mock** mode (offline real-signature settlement) — the
degraded path documented in `ARCHITECTURE.md §5`.

---

## Step 3 — Register & list the ASP on OKX.AI

1. Decide the final public name (run a quick marketplace + web search to avoid a
   collision; working title is "ASP PreFlight", shortlist LaunchCheck /
   GreenLight).
2. Follow the OKX.AI A2MCP listing flow (see `okx.ai/tutorial/asp`):
   `npx skills add okx/onchainos-skills` then follow the registration prompts,
   or the marketplace's "list a service" UI.
   - **Endpoint:** `https://<preflight>/mcp/`
   - **Type:** A2MCP, **Price:** free
   - **Tool:** `preflight_run` (args: `target_url`, `paid_tool`, `price_usdt`,
     `tools`, `sample_args`)
   - **Description:** from `PROJECT.md` one-liner + the nine-check table.
3. **Self-test ceremony:** run PreFlight against its *own* public endpoint,
   confirm a green report, and put that report permalink in the listing as
   living proof.
4. Submit for review. Watch for the review result (SLA ≤24h) — this is why we
   submit by Jul 16 18:00, so a rejection can be fixed and resubmitted.

---

## Step 4 — Demo, X post, form

1. Record the ≤90s demo following `DEMO_SCRIPT.md` (broken → red → fix → green).
2. Post on X with **#OKXAI**, the demo video, and one line on the Pune/India
   builder angle. Link the live listing + a sample report.
3. Submit the Google Form before **Jul 17 23:59 UTC**.
4. GTM: drop "first N builders get a free PreFlight run" in the hackathon
   channels — every other entrant faces the same review you just de-risked.
