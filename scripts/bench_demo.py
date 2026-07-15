"""Bench demo: boot 3 vendor profiles + Bench, run a comparison through Bench's
own MCP surface (exactly as an okx.ai buyer agent would), print the ranking.
"""
import asyncio
import os
import pathlib
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
from tests.conftest import ServerThread  # noqa: E402

VENDORS = [
    ("cheap-thin", vendor(price=0.02, latency_ms=20, richness=0), 8861),
    ("mid-rich", vendor(price=0.05, latency_ms=120, richness=2), 8862),
    ("pricey-slow", vendor(price=0.15, latency_ms=400, richness=3), 8863),
]


async def main() -> int:
    servers = [ServerThread(bench_app, 8860)]
    servers += [ServerThread(app, port) for _, app, port in VENDORS]
    for s in servers:
        s.__enter__()
    try:
        urls = [f"http://127.0.0.1:{port}/mcp/" for _, _, port in VENDORS]
        async with Client("http://127.0.0.1:8860/mcp/") as bench:
            res = await bench.call_tool("compare_services", {
                "targets": urls, "paid_tool": "market_pulse",
                "task": "market pulse feed"})
            print(res.content[0].text)
        return 0
    finally:
        for s in servers:
            s.__exit__()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
