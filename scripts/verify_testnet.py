"""Testnet go/no-go — RUN THIS ON YOUR MACHINE, not in the build sandbox.

Proves real on-chain settlement end to end at zero cash cost:
  1. deploy BrokenBazaar with NETWORK=base-sepolia FACILITATOR=okx
  2. fund the payer EOA with faucet USDC (see RUNBOOK)
  3. run this script; it drives PreFlight against the live testnet fixture
     and prints the settlement reference / tx hash from C6.

Usage:
  PAYER_MODE=testnet \
  PAYER_PRIVATE_KEY=0x<throwaway testnet key> \
  BAZAAR_URL=https://<your-bazaar>/mcp/ \
  python scripts/verify_testnet.py
"""
import asyncio
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REQUIRED = ["PAYER_PRIVATE_KEY", "BAZAAR_URL"]


def _preflight_env_check() -> None:
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        sys.exit(f"Set these env vars first: {', '.join(missing)}")
    os.environ.setdefault("PAYER_MODE", "testnet")
    if os.environ["PAYER_MODE"] != "testnet":
        sys.exit("PAYER_MODE must be 'testnet' for this script")


async def main() -> int:
    _preflight_env_check()
    from preflight.runner import run_preflight, summary_markdown
    from preflight.config import settings

    url = os.environ["BAZAAR_URL"]
    print(f"→ Running PreFlight against {url} on base-sepolia…\n")
    report = await run_preflight(url, {
        "paid_tool": "market_pulse", "price_usdt": 0.05,
        "tools": ["ping", "market_pulse"],
    })
    print(summary_markdown(report, settings.base_url))
    c6 = next((r for r in report.results if r.id == "C6"), None)
    print("\n--- C6 settlement evidence ---")
    print(c6.evidence if c6 else "C6 missing")
    if report.tx_refs:
        print("\nSettlement references:", report.tx_refs)
        print("\n✅ GO: real testnet settlement confirmed.")
        return 0
    print("\n❌ NO-GO: no settlement reference captured. Check faucet balance and "
          "that the fixture was deployed with FACILITATOR=okx NETWORK=base-sepolia.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
