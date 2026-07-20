"""End-to-end: run the real suite over HTTP against live BrokenBazaar instances."""
import asyncio

import httpx
import pytest

from fixtures.broken_bazaar.app import create_app
from preflight.models import Status
from preflight.runner import run_preflight

CLAIMS = {
    "paid_tool": "market_pulse",
    "price_usdt": 0.05,
    "tools": ["ping", "market_pulse"],
}


def _status(report, cid):
    return {r.id: r.status for r in report.results}[cid]


def _run(url):
    return asyncio.run(run_preflight(url, dict(CLAIMS)))


def test_clean_fixture_all_green(server_factory):
    with server_factory(create_app(), 8901):
        report = _run("http://127.0.0.1:8901/mcp/")
    assert report.overall == "PASS", [(r.id, r.status, r.summary) for r in report.results]
    for cid in ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]:
        assert _status(report, cid) == Status.PASS, (cid, report.results)
    assert report.spend_usdt == pytest.approx(0.05)
    assert report.tx_refs, "settlement reference missing"


def test_price_bug_caught(server_factory):
    with server_factory(create_app(bug_price=True), 8902):
        report = _run("http://127.0.0.1:8902/mcp/")
    assert _status(report, "C4") == Status.PASS
    assert _status(report, "C5") == Status.FAIL
    assert report.overall == "FAIL"


def test_empty_delivery_bug_caught(server_factory):
    with server_factory(create_app(bug_empty=True), 8903):
        report = _run("http://127.0.0.1:8903/mcp/")
    assert _status(report, "C6") == Status.PASS  # payment itself works
    assert _status(report, "C7") == Status.FAIL  # but delivery is empty
    assert report.overall == "FAIL"


def test_naked_tool_reported_as_free_not_broken(server_factory):
    """A 'paid' tool that answers directly with 200 (no 402 at all) reads as a
    free service, not a broken paywall — C4 is a WARN, not a FAIL, and the run
    isn't gated on it."""
    naked = create_app().inner_app  # bypass the paywall wrapper entirely
    with server_factory(naked, 8904):
        report = _run("http://127.0.0.1:8904/mcp/")
    assert _status(report, "C4") == Status.WARN
    c4 = {r.id: r for r in report.results}["C4"]
    assert "this service appears to be free — no payment required" in c4.summary
    assert report.overall == "PASS"


def test_report_page_renders(server_factory):
    from fastapi.testclient import TestClient
    from preflight.app import app as preflight_app

    with server_factory(create_app(), 8905):
        report = _run("http://127.0.0.1:8905/mcp/")
    client = TestClient(preflight_app)
    page = client.get(f"/report/{report.id}")
    assert page.status_code == 200
    assert "PASS" in page.text and "C6" in page.text
    assert client.get("/report/doesnotexist").status_code == 404
    assert client.get("/healthz").json()["ok"] is True
    head = client.head("/healthz")
    assert head.status_code == 200
    assert head.content == b""


def test_bazaar_healthz(server_factory):
    with server_factory(create_app(), 8906):
        resp = httpx.get("http://127.0.0.1:8906/healthz")
        head = httpx.head("http://127.0.0.1:8906/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert head.status_code == 200
    assert head.content == b""
