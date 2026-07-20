"""SQLite persistence: one reports table plus a payments intent ledger."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager

from .config import settings
from .models import CheckResult, Report, Status

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, created_at TEXT, target_url TEXT,
  claims_json TEXT, results_json TEXT, overall TEXT,
  spend_usdt REAL, tx_refs_json TEXT);
CREATE TABLE IF NOT EXISTS payments (
  intent_id TEXT PRIMARY KEY, report_id TEXT, amount_usdt REAL,
  network TEXT, nonce TEXT, created_at TEXT);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def new_report_id() -> str:
    return secrets.token_urlsafe(8).replace("-", "x").replace("_", "y")


def save_report(r: Report) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?,?)",
            (r.id, r.created_at, r.target_url, json.dumps(r.claims),
             r.results_json(), r.overall, r.spend_usdt, json.dumps(r.tx_refs)),
        )


def load_report(report_id: str) -> Report | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if row is None:
        return None
    results = [
        CheckResult(d["id"], d["name"], Status(d["status"]), d["summary"],
                    d.get("evidence", {}), d.get("duration_ms", 0))
        for d in json.loads(row["results_json"])
    ]
    return Report(row["id"], row["created_at"], row["target_url"],
                  json.loads(row["claims_json"]), results, row["overall"],
                  row["spend_usdt"], json.loads(row["tx_refs_json"]))


_COMPARISONS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS comparisons ("
    "id TEXT PRIMARY KEY, created_at TEXT, task TEXT, "
    "candidates_json TEXT, winner_url TEXT, total_spend_usdt REAL, tx_refs_json TEXT)")

# Columns added after the initial release — applied to any pre-existing table
# on every connection so an older on-disk db picks them up without a migration step.
_COMPARISONS_NEW_COLUMNS = (
    ("paid_tool", "TEXT"),
    ("paid_tool_inferred", "INTEGER"),
    ("no_paid_tool", "INTEGER"),
    ("target_tools_json", "TEXT"),
)


def _ensure_comparisons_table(con) -> None:
    con.execute(_COMPARISONS_SCHEMA)
    for name, coltype in _COMPARISONS_NEW_COLUMNS:
        try:
            con.execute(f"ALTER TABLE comparisons ADD COLUMN {name} {coltype}")
        except sqlite3.OperationalError:
            pass  # column already exists


def save_comparison(comp) -> None:
    """Persist a Bench comparison. Candidates stored as JSON blob."""
    import json as _json
    from dataclasses import asdict
    with _conn() as con:
        _ensure_comparisons_table(con)
        con.execute(
            "INSERT OR REPLACE INTO comparisons "
            "(id, created_at, task, candidates_json, winner_url, total_spend_usdt, "
            "tx_refs_json, paid_tool, paid_tool_inferred, no_paid_tool, target_tools_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (comp.id, comp.created_at, comp.task,
             _json.dumps([asdict(c) for c in comp.candidates]),
             comp.winner_url, comp.total_spend_usdt, _json.dumps(comp.tx_refs),
             comp.paid_tool, int(comp.paid_tool_inferred), int(comp.no_paid_tool),
             _json.dumps(comp.target_tools)))


def load_comparison(comp_id: str):
    import json as _json
    from .bench import Candidate, Comparison
    with _conn() as con:
        _ensure_comparisons_table(con)
        row = con.execute("SELECT * FROM comparisons WHERE id=?", (comp_id,)).fetchone()
    if row is None:
        return None
    import dataclasses
    _fields = {f.name for f in dataclasses.fields(Candidate)}
    cands = [Candidate(**{k: v for k, v in d.items() if k in _fields})
             for d in _json.loads(row["candidates_json"])]
    keys = row.keys()
    return Comparison(
        row["id"], row["created_at"], row["task"], cands,
        row["winner_url"], row["total_spend_usdt"], _json.loads(row["tx_refs_json"]),
        paid_tool=row["paid_tool"] if "paid_tool" in keys else None,
        paid_tool_inferred=bool(row["paid_tool_inferred"]) if "paid_tool_inferred" in keys and row["paid_tool_inferred"] is not None else False,
        no_paid_tool=bool(row["no_paid_tool"]) if "no_paid_tool" in keys and row["no_paid_tool"] is not None else False,
        target_tools=_json.loads(row["target_tools_json"]) if "target_tools_json" in keys and row["target_tools_json"] is not None else {},
    )
