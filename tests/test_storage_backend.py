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


def test_turso_replica_uses_real_file_path_not_memory(monkeypatch, tmp_path):
    """The embedded replica must be given a real file — libsql's WAL-based
    replica fails with 'wal_insert_begin failed' against ':memory:'."""
    import libsql

    seen = {}

    def fake_connect(path, **kwargs):
        seen["path"] = path
        raise RuntimeError("stop before any real network call")

    monkeypatch.setattr(libsql, "connect", fake_connect)
    replica_path = str(tmp_path / "replica.db")
    monkeypatch.setattr(config_module, "settings", dataclasses.replace(
        config_module.settings, turso_database_url="libsql://example.turso.io",
        turso_auth_token="some-token", turso_replica_path=replica_path,
        db_path=str(tmp_path / "fallback.db")))

    report = Report(id="path-check", created_at=now_iso(), target_url="http://x.example/mcp/",
                    claims={}, results=[], overall="PASS")
    store.save_report(report)  # must not raise — falls back to sqlite

    assert seen["path"] == replica_path
    assert seen["path"] != ":memory:"


def test_turso_connect_failure_falls_back_to_sqlite_without_500ing(monkeypatch, tmp_path):
    import libsql

    def raising_connect(path, **kwargs):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(libsql, "connect", raising_connect)
    db_file = tmp_path / "fallback_on_connect_failure.db"
    monkeypatch.setattr(config_module, "settings", dataclasses.replace(
        config_module.settings, turso_database_url="libsql://example.turso.io",
        turso_auth_token="some-token", turso_replica_path=str(tmp_path / "replica.db"),
        db_path=str(db_file)))

    report = Report(id="connect-fail-1", created_at=now_iso(), target_url="http://x.example/mcp/",
                    claims={}, results=[], overall="PASS", spend_usdt=0.01)
    store.save_report(report)  # must not raise

    assert db_file.exists()  # actually persisted, just via the fallback
    loaded = store.load_report("connect-fail-1")
    assert loaded is not None and loaded.spend_usdt == 0.01


def test_turso_sync_failure_falls_back_to_sqlite_without_500ing(monkeypatch, tmp_path):
    import libsql

    class _FakeConn:
        def sync(self):
            raise RuntimeError("sync failed: server unavailable")

        def close(self):
            pass

    monkeypatch.setattr(libsql, "connect", lambda path, **kwargs: _FakeConn())
    db_file = tmp_path / "fallback_on_sync_failure.db"
    monkeypatch.setattr(config_module, "settings", dataclasses.replace(
        config_module.settings, turso_database_url="libsql://example.turso.io",
        turso_auth_token="some-token", turso_replica_path=str(tmp_path / "replica.db"),
        db_path=str(db_file)))

    report = Report(id="sync-fail-1", created_at=now_iso(), target_url="http://x.example/mcp/",
                    claims={}, results=[], overall="PASS", spend_usdt=0.02)
    store.save_report(report)  # must not raise

    assert db_file.exists()
    loaded = store.load_report("sync-fail-1")
    assert loaded is not None and loaded.spend_usdt == 0.02


def test_turso_push_sync_failure_still_commits_locally(monkeypatch, tmp_path):
    """The initial pull-sync succeeds, but the post-commit push-sync fails —
    the write must still land (in the local replica file) rather than raise."""
    import libsql

    class _FlakyPushConn:
        def __init__(self):
            self.sync_calls = 0

        def executescript(self, sql):
            pass

        def execute(self, sql, params=()):
            pass

        def commit(self):
            pass

        def sync(self):
            self.sync_calls += 1
            if self.sync_calls > 1:
                raise RuntimeError("push-sync failed: server unavailable")

        def close(self):
            pass

    monkeypatch.setattr(libsql, "connect", lambda path, **kwargs: _FlakyPushConn())
    monkeypatch.setattr(config_module, "settings", dataclasses.replace(
        config_module.settings, turso_database_url="libsql://example.turso.io",
        turso_auth_token="some-token", turso_replica_path=str(tmp_path / "replica.db"),
        db_path=str(tmp_path / "unused.db")))

    report = Report(id="push-fail-1", created_at=now_iso(), target_url="http://x.example/mcp/",
                    claims={}, results=[], overall="PASS")
    store.save_report(report)  # must not raise despite the push-sync failure


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
