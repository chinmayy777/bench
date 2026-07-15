"""Bench comparison engine end-to-end tests."""
import asyncio

from fixtures.broken_bazaar.vendor_sim import create_app as vendor
from preflight.bench import compare_services
from preflight.store import load_comparison


def _urls(ports):
    return [f"http://127.0.0.1:{p}/mcp/" for p in ports]


def test_compare_ranks_by_value(server_factory):
    with server_factory(vendor(price=0.02, latency_ms=10, richness=0), 8851), \
         server_factory(vendor(price=0.05, latency_ms=60, richness=2), 8852), \
         server_factory(vendor(price=0.15, latency_ms=300, richness=3), 8853):
        comp = asyncio.run(compare_services(
            _urls([8851, 8852, 8853]), paid_tool="market_pulse", task="feed"))
    assert len(comp.candidates) == 3
    assert all(c.purchased for c in comp.candidates)
    assert comp.winner_url is not None
    # the overpriced+slow vendor must not win
    assert "8853" not in comp.winner_url
    # scores are ordered descending among usable
    scores = [c.value_score for c in comp.candidates if c.usable]
    assert scores == sorted(scores, reverse=True)


def test_compare_flags_broken_candidate(server_factory):
    from fixtures.broken_bazaar.app import create_app as bazaar
    with server_factory(vendor(price=0.05, latency_ms=20, richness=2), 8854), \
         server_factory(bazaar(bug_empty=True), 8855):
        comp = asyncio.run(compare_services(
            _urls([8854, 8855]), paid_tool="market_pulse", task="feed"))
    good = [c for c in comp.candidates if "8854" in c.target_url][0]
    bad = [c for c in comp.candidates if "8855" in c.target_url][0]
    assert good.usable
    assert not bad.usable  # paid-but-empty is not usable
    assert comp.winner_url and "8854" in comp.winner_url


def test_compare_persists_and_reloads(server_factory):
    with server_factory(vendor(price=0.03, latency_ms=15, richness=1), 8856), \
         server_factory(vendor(price=0.06, latency_ms=40, richness=2), 8857):
        comp = asyncio.run(compare_services(_urls([8856, 8857]), paid_tool="market_pulse"))
    again = load_comparison(comp.id)
    assert again is not None
    assert again.winner_url == comp.winner_url
    assert len(again.candidates) == 2


def test_compare_requires_two_targets():
    import pytest
    with pytest.raises(ValueError):
        asyncio.run(compare_services(["http://127.0.0.1:9/mcp/"]))
