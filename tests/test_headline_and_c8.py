"""Two related honesty fixes:

1. C8 (latency sampler) must not crash when a target 402-gates every call,
   including the bare `tools/list` used to sample latency — every real OKX
   ASP does this now. It should record the endpoint as gated/expected and
   still produce a real latency reading from the 402 round-trip.
2. The overall PASS/FAIL headline must never say PASS over a check that
   crashed, or over a payable challenge (C4 PASS) whose purchase was never
   actually settled (C6 not PASS) — a skip is not a pass.
"""
from __future__ import annotations

import asyncio

from preflight.models import CheckResult, Status
from preflight.runner import _compute_overall, run_preflight
from test_x402_probe import _HeaderChallengeApp

CLAIMS = {"paid_tool": "market_pulse", "price_usdt": 0.05, "tools": ["ping", "market_pulse"]}


def _by_id(report):
    return {r.id: r for r in report.results}


class TestC8GatedEndpoint:
    def test_c8_does_not_crash_on_a_universally_gated_target(self, server_factory):
        """_HeaderChallengeApp 402s every request, including the bare
        `tools/list` C8 uses to sample latency — this used to raise an
        unhandled httpx.HTTPStatusError all the way out of the check."""
        with server_factory(_HeaderChallengeApp(), 8981):
            report = asyncio.run(run_preflight("http://127.0.0.1:8981/", {}))

        c8 = _by_id(report)["C8"]
        assert c8.status in (Status.PASS, Status.WARN)
        assert "check crashed" not in c8.summary
        assert c8.evidence["gated"] is True
        assert len(c8.evidence["samples_ms"]) == 3
        assert all(s >= 0 for s in c8.evidence["samples_ms"])
        assert c8.evidence["p50_ms"] >= 0

    def test_c8_unaffected_on_a_normal_target(self, server_factory):
        """A target that answers list_tools() normally must keep the old
        behavior exactly — no 'gated' framing, no fallback timing path."""
        from fixtures.broken_bazaar.app import create_app

        with server_factory(create_app(), 8982):
            report = asyncio.run(run_preflight(
                "http://127.0.0.1:8982/mcp/", dict(CLAIMS)))

        c8 = _by_id(report)["C8"]
        assert c8.status == Status.PASS
        assert c8.evidence["gated"] is False
        assert "x402-gated" not in c8.summary


class TestOverallHeadlineHonesty:
    """Unit tests directly against _compute_overall — fast, exhaustive
    coverage of the decision table without spinning up a server per case."""

    @staticmethod
    def _result(cid, status, summary="x"):
        return CheckResult(cid, cid, status, summary)

    def test_all_pass_is_pass(self):
        results = [self._result(c, Status.PASS) for c in
                  ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]]
        assert _compute_overall(results) == "PASS"

    def test_any_fail_anywhere_blocks_pass_even_outside_old_gating_set(self):
        """C8/C9 were never in the old hard-coded gating set — a crash there
        used to be invisible to the headline. It must not be anymore."""
        results = [self._result(c, Status.PASS) for c in
                  ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C9"]]
        results.append(self._result("C8", Status.FAIL, "check crashed: boom"))
        assert _compute_overall(results) == "FAIL"

    def test_payable_challenge_with_purchase_skipped_is_fail(self):
        """C4 PASS (a real, payable 402 existed) but C6 SKIP (never actually
        paid) — the purchase this product exists to verify never happened."""
        results = [
            self._result("C1", Status.PASS), self._result("C2", Status.PASS),
            self._result("C4", Status.PASS),
            self._result("C6", Status.SKIP, "payment not attempted: no key"),
            self._result("C7", Status.SKIP, "no settled paid call to inspect"),
            self._result("C8", Status.PASS), self._result("C9", Status.PASS),
        ]
        assert _compute_overall(results) == "FAIL"

    def test_payable_challenge_missing_c6_entirely_is_fail(self):
        """Defensive: no C6 result at all alongside a C4 PASS must not read
        as an accidental pass-by-default."""
        results = [self._result("C4", Status.PASS)]
        assert _compute_overall(results) == "FAIL"

    def test_free_target_c4_warn_c6_skip_is_still_pass(self):
        """A genuinely free service (C4 WARN, not PASS) has nothing to buy —
        C6 skipping is correct, not a required step being skipped."""
        results = [
            self._result("C1", Status.PASS),
            self._result("C4", Status.WARN, "free — no payment required"),
            self._result("C6", Status.SKIP, "no challenge to pay — free"),
            self._result("C8", Status.PASS),
        ]
        assert _compute_overall(results) == "PASS"

    def test_unsupported_network_c4_warn_c6_skip_is_still_pass(self):
        """C4 WARN for an unsupported network is an environment limitation,
        not a failure — C6 skipping as a result is expected, not dishonest."""
        results = [
            self._result("C1", Status.PASS),
            self._result("C4", Status.WARN, "network not supported by our payer"),
            self._result("C6", Status.SKIP, "no challenge to pay — unsupported network"),
        ]
        assert _compute_overall(results) == "PASS"

    def test_payable_challenge_paid_and_settled_is_pass(self):
        results = [
            self._result("C1", Status.PASS), self._result("C4", Status.PASS),
            self._result("C6", Status.PASS), self._result("C7", Status.PASS),
        ]
        assert _compute_overall(results) == "PASS"


class TestHeadlineEndToEnd:
    def test_disabled_payer_with_payable_challenge_fails_the_run(self, server_factory, monkeypatch):
        """End-to-end reproduction of the exact bug report: a real payable
        challenge (C4 PASS) whose purchase never happens (payer disabled)
        must not be reported as an overall PASS."""
        import dataclasses
        from preflight import config as config_module
        from preflight import payer as payer_module

        monkeypatch.setattr(config_module, "settings",
                            dataclasses.replace(config_module.settings, payer_mode="off"))
        monkeypatch.setattr(payer_module, "settings", config_module.settings)

        from fixtures.broken_bazaar.app import create_app
        with server_factory(create_app(), 8983):
            report = asyncio.run(run_preflight("http://127.0.0.1:8983/mcp/", dict(CLAIMS)))

        results = _by_id(report)
        assert results["C4"].status == Status.PASS
        assert results["C6"].status == Status.SKIP
        assert report.overall == "FAIL"
