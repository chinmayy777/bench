"""Core result types."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


@dataclass
class CheckResult:
    id: str
    name: str
    status: Status
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class Report:
    id: str
    created_at: str
    target_url: str
    claims: dict[str, Any]
    results: list[CheckResult]
    overall: str
    spend_usdt: float = 0.0
    tx_refs: list[str] = field(default_factory=list)

    def results_json(self) -> str:
        return json.dumps([r.to_dict() for r in self.results])


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
