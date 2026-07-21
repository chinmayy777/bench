#!/usr/bin/env python3
"""Bridge: turn a real Tender (Bench) comparison into an OKX.AI task deliverable.

Calls the DEPLOYED bench's compare_services tool over MCP as a client and
writes a markdown deliverable summarizing the ranked comparison for a given
marketplace job. Never spins up a local bench and never re-implements the
ranking pipeline — the deployed compare_services call, and the public
permalink page it returns, are the only sources of truth used here.
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys
import time
from dataclasses import dataclass

import httpx
from fastmcp import Client

DEFAULT_BENCH_URL = "https://bench-pyfg.onrender.com/mcp/"
WARN_PREFIX = "⚠️ "

_PERMALINK_RE = re.compile(r"Full comparison:\s*(\S+)")
_TX_HREF_RE = re.compile(r'href="https://sepolia\.basescan\.org/tx/([0-9a-fA-Fx]+)"')
_VERDICT_RE = re.compile(r'<div class="verdict-line">(.*?)</div>', re.S)
_WINNER_RE = re.compile(r"\*\*Best value:\s*(\S+)\*\*")
_INFERRED_RE = re.compile(r"inferred as `([^`]+)`")


@dataclass
class Row:
    service: str
    score: str
    price: str
    latency: str
    delivered: str
    wake: str
    status: str
    tx_hash: str | None = None
    verdict: str | None = None

    @property
    def usable(self) -> bool:
        return self.status.startswith("✅")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job-id", required=True, help="Marketplace jobId this deliverable is for")
    p.add_argument("--targets", required=True,
                   help="Comma-separated ASP MCP endpoint URLs to compare (2-5)")
    p.add_argument("--paid-tool", default=None,
                   help="Paid tool name to buy from each target; omit to let Tender infer it")
    p.add_argument("--task", default=None, help="Short label for the comparison task")
    p.add_argument("--bench-url", default=DEFAULT_BENCH_URL,
                   help="Deployed Tender/Bench MCP endpoint (never a local instance)")
    p.add_argument("--out", default=None,
                   help="Output markdown path (default ./deliverables/<job-id>.md)")
    return p.parse_args(argv)


async def _call_bench(bench_url: str, targets: list[str], paid_tool: str | None,
                      task: str | None) -> str:
    """Call the deployed bench's compare_services tool over MCP, as a client."""
    call_args = {"targets": targets, "task": task or ""}
    if paid_tool:
        call_args["paid_tool"] = paid_tool
    async with Client(bench_url, timeout=180.0) as client:
        res = await client.call_tool("compare_services", call_args)
    text = next((getattr(b, "text", None) for b in res.content if getattr(b, "text", None)), None)
    if text is None:
        raise RuntimeError(f"compare_services returned no text content: {res!r}")
    return text


def _fetch_permalink_extras(permalink: str) -> dict:
    """Best-effort fetch of the public permalink page for data the markdown
    response doesn't carry: settlement tx hashes and each candidate's own
    honest verdict line (so we never have to judge an axis ourselves)."""
    try:
        resp = httpx.get(permalink, timeout=20.0)
        resp.raise_for_status()
    except httpx.HTTPError:
        return {"tx_hashes": [], "verdicts": []}
    html = resp.text
    return {
        "tx_hashes": _TX_HREF_RE.findall(html),
        "verdicts": [v.strip() for v in _VERDICT_RE.findall(html)],
    }


def _parse_table(md: str) -> tuple[list[str], list[list[str]]]:
    lines = [ln for ln in md.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return [], []
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines[2:]]
    return header, rows


def _build_rows(md: str, permalink: str | None) -> tuple[list[Row], bool]:
    """Returns (rows, no_paid_tool). tx hash / verdict are attached to usable
    rows in table order, pulled from the permalink page in the same order.

    Cells are looked up by header name, not fixed position — Bench's scorecard
    has grown columns before (e.g. a Shape column for HTTP-vs-MCP candidates)
    and will again; positional unpacking would silently misalign every field."""
    header, raw_rows = _parse_table(md)
    if "Tools exposed" in header:
        return [], True
    if not raw_rows:
        return [], False

    extras = _fetch_permalink_extras(permalink) if permalink else {"tx_hashes": [], "verdicts": []}
    tx_iter = iter(extras["tx_hashes"])
    verdict_iter = iter(extras["verdicts"])

    idx = {name: i for i, name in enumerate(header)}

    def cell(cells: list[str], name: str, default: str = "—") -> str:
        i = idx.get(name)
        return cells[i] if i is not None and i < len(cells) else default

    rows = []
    for cells in raw_rows:
        service = cell(cells, "Service", "")
        score = cell(cells, "Score")
        price = cell(cells, "Price")
        latency = cell(cells, "Latency")
        delivered = cell(cells, "Delivered")
        wake = cell(cells, "Wake")
        status = cell(cells, "Status")
        usable = status.startswith("✅")
        tx = next(tx_iter, None) if usable else None
        verdict = next(verdict_iter, None) if usable else None
        rows.append(Row(service, score, price, latency, delivered, wake, status, tx, verdict))
    return rows, False


def _extract_summary(md: str) -> dict:
    m = _WINNER_RE.search(md)
    m2 = _INFERRED_RE.search(md)
    return {
        "winner": m.group(1) if m else None,
        "no_usable": "No usable service among the candidates" in md,
        "no_paid_tool": "No paid tool was named, and none could be inferred" in md,
        "inferred": m2.group(1) if m2 else None,
    }


def _one_line_answer(summary: dict, rows: list[Row], no_paid_tool: bool,
                     call_error: str | None) -> str:
    if call_error:
        return f"No winner can be named — the comparison call failed: {call_error}"
    if no_paid_tool:
        return ("No winner can be named — no paid tool was supplied and none could be "
               "inferred across the given targets")
    if summary["winner"]:
        row = next((r for r in rows if r.service == summary["winner"]), None)
        why = (row.verdict if row and row.verdict
               else "best overall value across price, latency, and delivery")
        return f"{summary['winner']} is the best value — {why}"
    return "No winner can be named — no candidate was usable"


def render_deliverable(*, job_id: str, permalink: str | None, summary: dict,
                       rows: list[Row], no_paid_tool: bool, raw_md: str,
                       run_at_iso: str, call_error: str | None,
                       one_liner: str) -> str:
    lines = [f"# Task deliverable — job `{job_id}`", "", f"**Answer:** {one_liner}", ""]

    # ranked scorecard (or the honest substitute when there's nothing to rank)
    lines.append("## Scorecard")
    lines.append("")
    if call_error:
        lines.append(f"Not available — the comparison call failed: {call_error}")
    elif no_paid_tool:
        lines.append("No ranking was produced — no paid tool was named or inferable. "
                     "Tools each target actually exposes:")
        lines.append("")
        _, raw_rows = _parse_table(raw_md)
        lines.append("| Service | Tools exposed |")
        lines.append("|---|---|")
        for cells in raw_rows:
            cells = (cells + ["", ""])[:2]
            lines.append(f"| {cells[0]} | {cells[1]} |")
    elif not rows:
        lines.append("No candidates were returned.")
    else:
        lines.append("| Service | Score | Price | Latency | Delivered | Wake | Status |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(f"| {r.service} | {r.score} | {r.price} | {r.latency} | "
                         f"{r.delivered} | {r.wake} | {r.status} |")
    lines.append("")

    # scoring formula, stated explicitly
    lines.append("## Scoring formula")
    lines.append("")
    lines.append("value = price **45%** · latency **25%** · delivery **30%**")
    lines.append("")

    # excluded targets, full plain-language reason
    lines.append("## Excluded targets")
    lines.append("")
    if call_error:
        lines.append(f"All targets — the comparison call itself failed: {call_error}")
    elif no_paid_tool:
        lines.append("No target was probed or purchased from — see the tool listing above.")
    else:
        excluded = [r for r in rows if not r.usable]
        if not excluded:
            lines.append("None — every target was usable.")
        else:
            for r in excluded:
                reason = (r.status[len(WARN_PREFIX):] if r.status.startswith(WARN_PREFIX)
                          else r.status)
                lines.append(f"- **{r.service}**: {reason}")
    lines.append("")

    # settlement transactions, one clickable link per purchase
    lines.append("## Settlement transactions")
    lines.append("")
    tx_rows = [r for r in rows if r.tx_hash] if rows else []
    if not tx_rows:
        lines.append("None — no purchase settled.")
    else:
        for r in tx_rows:
            lines.append(f"- {r.service}: https://sepolia.basescan.org/tx/{r.tx_hash}")
    lines.append("")

    # public permalink
    lines.append("## Full comparison")
    lines.append("")
    lines.append(permalink or "Not available — the comparison did not produce a permalink.")
    lines.append("")

    # UTC timestamp of the run
    lines.append("## Run timestamp (UTC)")
    lines.append("")
    lines.append(run_at_iso)
    lines.append("")

    return "\n".join(lines)


def build_deliverable(job_id: str, targets: list[str], paid_tool: str | None,
                      task: str | None, bench_url: str) -> tuple[str, str]:
    """Run the full pipeline and return (markdown_doc, one_line_answer).

    Never raises on a failed comparison — a failure is evidence to report,
    not an exception to propagate. The deliverable is always written."""
    run_at_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    call_error = None
    raw_md = None
    try:
        raw_md = asyncio.run(_call_bench(bench_url, targets, paid_tool, task))
    except Exception as e:
        call_error = f"{type(e).__name__}: {e}"

    if raw_md is not None and raw_md.lstrip().startswith("❌"):
        call_error = raw_md.strip()
        raw_md = None

    permalink = None
    summary = {"winner": None, "no_usable": False, "no_paid_tool": False, "inferred": None}
    rows: list[Row] = []
    no_paid_tool = False

    if raw_md is not None:
        m = _PERMALINK_RE.search(raw_md)
        permalink = m.group(1) if m else None
        summary = _extract_summary(raw_md)
        rows, no_paid_tool = _build_rows(raw_md, permalink)

    one_liner = _one_line_answer(summary, rows, no_paid_tool, call_error)
    doc = render_deliverable(
        job_id=job_id, permalink=permalink, summary=summary, rows=rows,
        no_paid_tool=no_paid_tool, raw_md=raw_md or "", run_at_iso=run_at_iso,
        call_error=call_error, one_liner=one_liner,
    )
    return doc, one_liner


def main(argv=None) -> int:
    args = _parse_args(argv)
    targets = [u.strip() for u in args.targets.split(",") if u.strip()]
    if len(targets) < 2:
        print("--targets needs at least 2 comma-separated URLs", file=sys.stderr)
        return 1

    out_path = (pathlib.Path(args.out) if args.out
               else pathlib.Path(f"./deliverables/{args.job_id}.md"))

    doc, one_liner = build_deliverable(
        job_id=args.job_id, targets=targets, paid_tool=args.paid_tool,
        task=args.task, bench_url=args.bench_url,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc)

    print(doc)
    print()
    print(f'onchainos agent deliver {args.job_id} --file {out_path} '
         f'--message "{one_liner}" --agent-id 6337')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
