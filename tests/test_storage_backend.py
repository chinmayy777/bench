"""Storage backend selection: Turso (libSQL) vs the local sqlite3 fallback.

Only the fallback path is exercised here — no real Turso credentials exist in
this environment. The point is to prove that when TURSO_DATABASE_URL /
TURSO_AUTH_TOKEN are absent (or only partially set), storage behaves exactly
as the original local-sqlite-only store did, unchanged.
"""
import dataclasses

from preflight import config as config_module
from preflight import store
from preflight.models import CheckResult, Report, Status, now_iso


def _with_turso_settings(monkeypatch, *, database_url="", auth_token="", db_path=None):
    kwargs = {"turso_database_url": database_url, "turso_auth_token": auth_token}
    if db_path is not None:
        kwargs["db_path"] = db_path
    monkeypatch.setattr(config_module, "settings",
                        dataclasses.replace(config_module.settings, **kwargs))


def test_using_turso_false_when_both_unset(monkeypatch):
    _with_turso_settings(monkeypatch, database_url="", auth_token="")
    assert store._using_turso() is False


def test_using_turso_false_when_only_url_set(monkeypatch):
    _with_turso_settings(monkeypatch, database_url="libsql://example.turso.io", auth_token="")
    assert store._using_turso() is False


def test_using_turso_false_when_only_token_set(monkeypatch):
    _with_turso_settings(monkeypatch, database_url="", auth_token="some-token")
    assert store._using_turso() is False


def test_using_turso_true_when_both_set(monkeypatch):
    _with_turso_settings(monkeypatch, database_url="libsql://example.turso.io",
                        auth_token="some-token")
    assert store._using_turso() is True


def test_fallback_report_roundtrip_uses_local_sqlite_file(monkeypatch, tmp_path):
    db_file = tmp_path / "fallback_reports.db"
    _with_turso_settings(monkeypatch, database_url="", auth_token="", db_path=str(db_file))

    report = Report(
        id="fallback-report-1", created_at=now_iso(), target_url="http://x.example/mcp/",
        claims={"paid_tool": "market_pulse"},
        results=[CheckResult("C1", "Reachability", Status.PASS, "reachable in 10 ms")],
        overall="PASS", spend_usdt=0.05, tx_refs=["mock:0xabc"],
    )
    store.save_report(report)

    assert db_file.exists()  # a real local sqlite file was written, not Turso

    loaded = store.load_report("fallback-report-1")
    assert loaded is not None
    assert loaded.overall == "PASS"
    assert loaded.spend_usdt == 0.05
    assert loaded.tx_refs == ["mock:0xabc"]
    assert loaded.results[0].id == "C1"
    assert loaded.results[0].status == Status.PASS


def test_fallback_comparison_roundtrip_uses_local_sqlite_file(monkeypatch, tmp_path):
    db_file = tmp_path / "fallback_comparisons.db"
    _with_turso_settings(monkeypatch, database_url="", auth_token="", db_path=str(db_file))

    from preflight.bench import Candidate, Comparison
    cand = Candidate(target_url="http://a.example/mcp/", reachable=True, purchased=True,
                     price_usdt=0.02, latency_ms=100, delivered_chars=50,
                     tx_ref="mock:0xdead", report_id="r1", notes=[],
                     value_score=90.0, verdict="Best value", wake_ms=50, woke=False)
    comp = Comparison(id="fallback-comp-1", created_at=now_iso(), task="t",
                      candidates=[cand], winner_url="http://a.example/mcp/",
                      total_spend_usdt=0.02, tx_refs=["mock:0xdead"],
                      paid_tool="market_pulse", paid_tool_inferred=True,
                      no_paid_tool=False, target_tools={"http://a.example/mcp/": ["market_pulse"]})
    store.save_comparison(comp)

    assert db_file.exists()  # a real local sqlite file was written, not Turso

    loaded = store.load_comparison("fallback-comp-1")
    assert loaded is not None
    assert loaded.winner_url == "http://a.example/mcp/"
    assert loaded.paid_tool == "market_pulse"
    assert loaded.paid_tool_inferred is True
    assert loaded.no_paid_tool is False
    assert loaded.candidates[0].tx_ref == "mock:0xdead"
