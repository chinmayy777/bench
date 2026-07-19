"""Bench demo: boot Bench + 3 local vendor profiles (or compare against your
own remote MCP URLs via --targets), run a comparison through Bench's own MCP
surface (exactly as an okx.ai buyer agent would), print the ranking plus each
candidate's wake_ms/woke from the wake phase.
"""
import argparse
import asyncio
import os
import pathlib
import re
import sys

os.environ.setdefault("ALLOW_LOCAL_TARGETS", "1")
os.environ.setdefault("PAYER_MODE", "mock")
os.environ.setdefault("PAYER_PRIVATE_KEY",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
os.environ.setdefault("DB_PATH", "/tmp/bench_golden.db")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fastmcp import Client  # noqa: E402
from fixtures.broken_bazaar.vendor_sim import create_app as vendor  # noqa: E402
from preflight.app import app as bench_app  # noqa: E402
from preflight.store import load_comparison  # noqa: E402
from tests.conftest import ServerThread  # noqa: E402

VENDORS = [
    ("cheap-thin", vendor(price=0.02, latency_ms=20, richness=0), 8861),
    ("mid-rich", vendor(price=0.05, latency_ms=120, richness=2), 8862),
    ("pricey-slow", vendor(price=0.15, latency_ms=400, richness=3), 8863),
]

_COMPARE_ID_RE = re.compile(r"/compare/(\S+)")


def _parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--targets",
        help="Comma-separated remote MCP endpoint URLs to compare, instead of "
             "the 3 local vendor fixtures (2-5 URLs).")
    p.add_argument("--paid-tool", default="market_pulse",
                   help="Paid tool name to buy from each target (default: market_pulse).")
    p.add_argument("--task", default="market pulse feed",
                   help="Short label for the comparison task.")
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    remote = [u.strip() for u in args.targets.split(",") if u.strip()] if args.targets else None
    if remote is not None and len(remote) < 2:
        print("--targets needs at least 2 comma-separated URLs", file=sys.stderr)
        return 1

    servers = [ServerThread(bench_app, 8860)]
    if remote is None:
        servers += [ServerThread(app, port) for _, app, port in VENDORS]
    for s in servers:
        s.__enter__()
    try:
        urls = remote or [f"http://127.0.0.1:{port}/mcp/" for _, _, port in VENDORS]
        async with Client("http://127.0.0.1:8860/mcp/") as bench:
            res = await bench.call_tool("compare_services", {
                "targets": urls, "paid_tool": args.paid_tool, "task": args.task})
            text = res.content[0].text
            print(text)

        m = _COMPARE_ID_RE.search(text)
        comp = load_comparison(m.group(1)) if m else None
        if comp:
            print("\nWake phase:")
            for c in comp.candidates:
                print(f"  {c.target_url}: wake_ms={c.wake_ms} woke={c.woke}")
        return 0
    finally:
        for s in servers:
            s.__exit__()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
