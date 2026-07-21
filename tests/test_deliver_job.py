"""scripts/deliver_job.py — mocked-bench-response tests.

Mocks only the deployed-bench MCP call and the permalink-page fetch (the two
network boundaries); everything else (parsing, rendering) runs for real.
"""
import re

from scripts import deliver_job

CANONICAL_MD = """# Bench comparison — canonical run

**Best value: https://vendor-cheap.onrender.com/mcp/**

| Service | Score | Price | Latency | Delivered | Wake | Status |
|---|---|---|---|---|---|---|
| https://vendor-cheap.onrender.com/mcp/ | 70.0 | 0.02 | 2265ms | 209c | 118ms | ✅ usable |
| https://vendor-mid.onrender.com/mcp/ | 66.7 | 0.05 | 3274ms | 441c | 155ms | ✅ usable |
| https://vendor-rich.onrender.com/mcp/ | 30.0 | 0.15 | 3789ms | 503c | 156ms | ✅ usable |

Total test spend: 0.22 (non-mainnet)

Full comparison: https://bench-pyfg.onrender.com/compare/6EL71yjJOgQ"""

CANONICAL_EXTRAS = {
    "tx_hashes": [
        "0x43081c445dbdd3f1cd3ed789d1c35d01272d6fa8ba0d86bc9321a21426cbc910",
        "0x253be6b4113f55a4b3eb7109ba8eb156841b85a1a1e26ca3b2b3a158efab3350",
        "0x793bd25dd2dc04e0309e723e82da0e6d2c5de37f81e0082381edd540d1ab66a4",
    ],
    "verdicts": [
        "Best value — lowest price and fastest",
        "Delivers 2.1× more data but costs 2.5× more and 45% more latency than the winner",
        "Delivers 2.4× more data but costs 7.5× more and 1.7× more latency than the winner",
    ],
}

DEAD_MD = """# Bench comparison — dead endpoint test

**No usable service among the candidates.**

| Service | Score | Price | Latency | Delivered | Wake | Status |
|---|---|---|---|---|---|---|
| https://price-alpha.onrender.com/mcp/ | — | — | 162ms | — | 166ms | ⚠️ no challenge to pay — target unreachable: unpaid call returned HTTP 404, expected 402 — host reports no-server (no live backend bound to this URL) |
| https://price-beta.onrender.com/mcp/ | — | — | 42ms | — | 157ms | ⚠️ no challenge to pay — target unreachable: unpaid call returned HTTP 404, expected 402 — host reports no-server (no live backend bound to this URL) |

Full comparison: https://bench-pyfg.onrender.com/compare/rDShGUgLa80"""


def _mock_bench(monkeypatch, md: str, extras: dict):
    async def fake_call_bench(bench_url, targets, paid_tool, task):
        fake_call_bench.calls.append((bench_url, targets, paid_tool, task))
        return md
    fake_call_bench.calls = []
    monkeypatch.setattr(deliver_job, "_call_bench", fake_call_bench)
    monkeypatch.setattr(deliver_job, "_fetch_permalink_extras", lambda permalink: extras)
    return fake_call_bench


def test_normal_ranking_case(monkeypatch):
    fake = _mock_bench(monkeypatch, CANONICAL_MD, CANONICAL_EXTRAS)

    doc, one_liner = deliver_job.build_deliverable(
        job_id="job-123",
        targets=["https://vendor-cheap.onrender.com/mcp/", "https://vendor-mid.onrender.com/mcp/",
                 "https://vendor-rich.onrender.com/mcp/"],
        paid_tool="market_pulse", task="canonical run",
        bench_url="https://bench-pyfg.onrender.com/mcp/",
    )

    # called the deployed bench, never a local one
    assert fake.calls == [("https://bench-pyfg.onrender.com/mcp/",
                          ["https://vendor-cheap.onrender.com/mcp/",
                           "https://vendor-mid.onrender.com/mcp/",
                           "https://vendor-rich.onrender.com/mcp/"],
                          "market_pulse", "canonical run")]

    assert "# Task deliverable — job `job-123`" in doc
    # one-line answer names the winner and quotes Bench's own honest verdict
    assert one_liner == ("https://vendor-cheap.onrender.com/mcp/ is the best value — "
                         "Best value — lowest price and fastest")
    assert f"**Answer:** {one_liner}" in doc

    # ranked scorecard, all three rows
    assert "| Service | Score | Price | Latency | Delivered | Wake | Status |" in doc
    assert "| https://vendor-cheap.onrender.com/mcp/ | 70.0 | 0.02 | 2265ms | 209c | 118ms | ✅ usable |" in doc
    assert "| https://vendor-mid.onrender.com/mcp/ | 66.7 | 0.05 | 3274ms | 441c | 155ms | ✅ usable |" in doc
    assert "| https://vendor-rich.onrender.com/mcp/ | 30.0 | 0.15 | 3789ms | 503c | 156ms | ✅ usable |" in doc

    # scoring formula stated explicitly
    assert "value = price **45%** · latency **25%** · delivery **30%**" in doc

    # nothing excluded
    assert "None — every target was usable." in doc

    # every settlement tx as a clickable basescan link
    for h in CANONICAL_EXTRAS["tx_hashes"]:
        assert f"https://sepolia.basescan.org/tx/{h}" in doc

    # permalink + UTC timestamp present
    assert "https://bench-pyfg.onrender.com/compare/6EL71yjJOgQ" in doc
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", doc)


def test_all_targets_unreachable_case(monkeypatch):
    fake = _mock_bench(monkeypatch, DEAD_MD, {"tx_hashes": [], "verdicts": []})

    doc, one_liner = deliver_job.build_deliverable(
        job_id="0xf4639adc76590ac17fd50b68bd0e9165abe91315a28887fbea2ff800defa5a99",
        targets=["https://price-alpha.onrender.com/mcp/", "https://price-beta.onrender.com/mcp/"],
        paid_tool="get_price", task="dead endpoint test",
        bench_url="https://bench-pyfg.onrender.com/mcp/",
    )

    assert fake.calls[0][2] == "get_price"

    # never silence, never a fabricated verdict — states plainly no winner
    assert one_liner == "No winner can be named — no candidate was usable"
    assert f"**Answer:** {one_liner}" in doc

    # excluded targets carry the FULL differentiated failure reason, not the
    # old generic "no challenge captured in C4" line
    assert ("target unreachable: unpaid call returned HTTP 404, expected 402 — "
           "host reports no-server (no live backend bound to this URL)") in doc
    assert "no challenge captured in C4" not in doc
    # appears once per target in the scorecard's Status column, and again in
    # the dedicated Excluded targets section
    assert doc.count("target unreachable: unpaid call returned HTTP 404") == 4

    # no purchase settled
    assert "None — no purchase settled." in doc

    # still a full status report: permalink + timestamp present, not silence
    assert "https://bench-pyfg.onrender.com/compare/rDShGUgLa80" in doc
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", doc)


SHAPE_MD = """# Bench comparison — shape column test

**Best value: https://plain-http.example/resource**

| Service | Shape | Score | Price | Latency | Delivered | Wake | Status |
|---|---|---|---|---|---|---|---|
| https://plain-http.example/resource | HTTP | 80.0 | 0.02 | 100ms | 50c | 10ms | ✅ usable |
| https://mcp-vendor.example/mcp/ | MCP | 40.0 | 0.10 | 300ms | 20c | 15ms | ✅ usable |

Full comparison: https://bench-pyfg.onrender.com/compare/shapeTest"""


def test_extra_shape_column_does_not_misalign_fields(monkeypatch):
    """Bench's scorecard gained a Shape column (HTTP-vs-MCP candidates); rows
    must still be read by header name, not silently shifted by position."""
    fake = _mock_bench(monkeypatch, SHAPE_MD, {"tx_hashes": [], "verdicts": []})

    doc, one_liner = deliver_job.build_deliverable(
        job_id="job-shape",
        targets=["https://plain-http.example/resource", "https://mcp-vendor.example/mcp/"],
        paid_tool=None, task="shape column test",
        bench_url="https://bench-pyfg.onrender.com/mcp/",
    )
    assert fake.calls  # bench was actually called

    assert "https://plain-http.example/resource is the best value" in one_liner
    # deliver_job re-renders its own condensed table (service/score/price/...),
    # dropping Shape — the real score/price must land in the right columns,
    # never shifted by the extra Shape column ("HTTP"/"MCP" must not appear
    # where Score belongs).
    assert "| https://plain-http.example/resource | 80.0 | 0.02 | 100ms | 50c | 10ms | ✅ usable |" in doc
    assert "| https://mcp-vendor.example/mcp/ | 40.0 | 0.10 | 300ms | 20c | 15ms | ✅ usable |" in doc


def test_bench_call_failure_still_produces_a_status_report(monkeypatch):
    async def raising_call_bench(bench_url, targets, paid_tool, task):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(deliver_job, "_call_bench", raising_call_bench)
    monkeypatch.setattr(deliver_job, "_fetch_permalink_extras",
                        lambda permalink: {"tx_hashes": [], "verdicts": []})

    doc, one_liner = deliver_job.build_deliverable(
        job_id="job-999", targets=["https://a.example/mcp/", "https://b.example/mcp/"],
        paid_tool="get_price", task="", bench_url="https://bench-pyfg.onrender.com/mcp/",
    )

    assert "the comparison call failed" in one_liner
    assert "connection refused" in doc
    assert "None — no purchase settled." in doc
    assert "Not available — the comparison did not produce a permalink." in doc
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", doc)
