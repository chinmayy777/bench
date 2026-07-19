"""A check's real failure reason must survive into evidence/logs and into
bench's per-candidate notes — never collapse into a generic message."""
import asyncio

from preflight.bench import _extract
from preflight.checks import RunContext, timed
from preflight.models import CheckResult, Report, Status, now_iso
from preflight.payer import Payer


def test_timed_wrapper_captures_crash_in_evidence():
    async def _boom(ctx):
        raise ConnectionError("connection refused")
    _boom.CHECK_ID, _boom.CHECK_NAME = "CX", "boom check"
    wrapped = timed(_boom)

    ctx = RunContext(target_url="http://x/mcp/", claims={}, payer=Payer(), http=None)
    res = asyncio.run(wrapped(ctx))

    assert res.status == Status.FAIL
    assert "ConnectionError: connection refused" in res.summary
    assert res.evidence["error"] == "ConnectionError: connection refused"


def _report_with_c6(c6: CheckResult) -> Report:
    c1 = CheckResult("C1", "reachable", Status.PASS, "reachable in 5 ms")
    return Report(id="r1", created_at=now_iso(), target_url="http://x/mcp/",
                  claims={}, results=[c1, c6], overall="FAIL")


def test_extract_surfaces_c6_failure_reason():
    c6 = CheckResult("C6", "settle", Status.FAIL,
                     "server rejected a validly signed payment (still 402) "
                     "— facilitator verify/settle is broken",
                     {"status_code": 402})
    m = _extract(_report_with_c6(c6))
    assert m["purchased"] is False
    assert m["purchase_error"] == c6.summary


def test_extract_purchase_error_none_when_purchased():
    c6 = CheckResult("C6", "settle", Status.PASS, "payment accepted, settled on mock")
    m = _extract(_report_with_c6(c6))
    assert m["purchased"] is True
    assert m["purchase_error"] is None
