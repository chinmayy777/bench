"""Definition of demo-done: the golden path, through the LISTED surface.

Boots PreFlight itself plus three BrokenBazaar states, then calls the
`preflight_run` MCP tool (exactly as an okx.ai buyer agent would) five
consecutive times per state. Exits non-zero on any deviation.
"""
import asyncio
import os
import pathlib
import sys

os.environ.setdefault("ALLOW_LOCAL_TARGETS", "1")
os.environ.setdefault("PAYER_MODE", "mock")
os.environ.setdefault("PAYER_PRIVATE_KEY",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
os.environ.setdefault("DB_PATH", "/tmp/preflight_golden.db")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fastmcp import Client  # noqa: E402
from fixtures.broken_bazaar.app import create_app  # noqa: E402
from preflight.app import app as preflight_app  # noqa: E402
from tests.conftest import ServerThread  # noqa: E402

SCENARIOS = [
    ("clean", create_app(), 8901, "PASS", None),
    ("price-bug", create_app(bug_price=True), 8902, "FAIL", "C5"),
    ("empty-bug", create_app(bug_empty=True), 8903, "FAIL", "C7"),
]
ARGS = {"paid_tool": "market_pulse", "price_usdt": 0.05,
        "tools": ["ping", "market_pulse"]}


async def one_round(rnd: int) -> bool:
    ok = True
    async with Client("http://127.0.0.1:8900/mcp/") as pf:
        for name, _app, port, want, want_fail in SCENARIOS:
            res = await pf.call_tool("preflight_run", {
                "target_url": f"http://127.0.0.1:{port}/mcp/", **ARGS})
            text = res.content[0].text
            verdict = "PASS" if f"PreFlight PASS" in text else "FAIL"
            fail_ok = want_fail is None or f"❌ **{want_fail}" in text
            good = verdict == want and fail_ok
            ok &= good
            print(f"  round {rnd} {name:<10} -> {verdict} "
                  f"{'(expected failure ' + want_fail + ' flagged)' if want_fail and fail_ok else ''}"
                  f"{'  ✅' if good else '  ❌ MISMATCH'}")
    return ok


async def main() -> int:
    servers = [ServerThread(preflight_app, 8900)]
    servers += [ServerThread(app, port) for _, app, port, _, _ in SCENARIOS]
    for s in servers:
        s.__enter__()
    try:
        all_ok = True
        for rnd in range(1, 6):
            all_ok &= await one_round(rnd)
        print("\nGOLDEN PATH:", "5/5 CLEAN ✅" if all_ok else "DEVIATION ❌")
        return 0 if all_ok else 1
    finally:
        for s in servers:
            s.__exit__()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
