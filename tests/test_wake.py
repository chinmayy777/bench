"""Wake phase: pre-warm each candidate's /healthz before probe/purchase."""
import asyncio
import dataclasses

from fixtures.broken_bazaar.vendor_sim import create_app as vendor
from preflight import config as config_module
from preflight.bench import compare_services


def _urls(ports):
    return [f"http://127.0.0.1:{p}/mcp/" for p in ports]


class _DelayedHealthz:
    """Wraps an ASGI app so GET /healthz answers only after a delay, like a
    cold-starting host. Everything else (including lifespan) passes through."""

    def __init__(self, app, delay_s: float):
        self.app = app
        self.delay_s = delay_s

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["method"] == "GET"
                and scope.get("path", "").rstrip("/") == "/healthz"):
            await asyncio.sleep(self.delay_s)
        return await self.app(scope, receive, send)


def _with_wake_timeout(monkeypatch, timeout_s: float):
    monkeypatch.setattr(config_module, "settings",
                        dataclasses.replace(config_module.settings, wake_timeout_s=timeout_s))


def test_wake_cold_start_succeeds_without_inflating_latency(server_factory, monkeypatch):
    _with_wake_timeout(monkeypatch, 10)
    cold = _DelayedHealthz(vendor(price=0.05, latency_ms=0, richness=1), delay_s=2.3)
    warm = vendor(price=0.05, latency_ms=0, richness=1)
    with server_factory(cold, 8871), server_factory(warm, 8872):
        comp = asyncio.run(compare_services(
            _urls([8871, 8872]), paid_tool="market_pulse", task="feed"))
    cold_c = [c for c in comp.candidates if "8871" in c.target_url][0]
    warm_c = [c for c in comp.candidates if "8872" in c.target_url][0]

    assert cold_c.usable  # still probed and purchased successfully after waking
    assert cold_c.wake_ms is not None and cold_c.wake_ms >= 2000
    assert cold_c.woke is True
    # measured latency comes only from the paid purchase, not the wake delay
    assert cold_c.latency_ms is not None and cold_c.latency_ms < 2000

    assert warm_c.woke is False


def test_wake_never_responds_excludes_target(server_factory, monkeypatch):
    _with_wake_timeout(monkeypatch, 1.2)
    good = vendor(price=0.05, latency_ms=0, richness=1)
    with server_factory(good, 8873):
        comp = asyncio.run(compare_services(
            # 8999 has nothing listening: connection refused on every attempt
            _urls([8873, 8999]), paid_tool="market_pulse", task="feed"))
    dead = [c for c in comp.candidates if "8999" in c.target_url][0]
    alive = [c for c in comp.candidates if "8873" in c.target_url][0]

    assert not dead.reachable
    assert not dead.usable  # excluded from ranking, same as any unreachable candidate
    assert dead.notes == ["no response within 1.2s — service may be down or cold-starting too slowly"]
    assert alive.usable
    assert comp.winner_url and "8873" in comp.winner_url


def test_wake_all_already_warm_nothing_else_changes(server_factory):
    with server_factory(vendor(price=0.02, latency_ms=10, richness=0), 8874), \
         server_factory(vendor(price=0.05, latency_ms=60, richness=2), 8875):
        comp = asyncio.run(compare_services(
            _urls([8874, 8875]), paid_tool="market_pulse", task="feed"))
    for c in comp.candidates:
        assert c.usable
        assert c.wake_ms is not None and c.wake_ms < 2000
        assert c.woke is False
    assert comp.winner_url is not None


def test_wake_disabled_skips_phase(server_factory, monkeypatch):
    monkeypatch.setattr(config_module, "settings",
                        dataclasses.replace(config_module.settings, wake_enabled=False))
    with server_factory(vendor(price=0.02, latency_ms=10, richness=0), 8876), \
         server_factory(vendor(price=0.05, latency_ms=60, richness=2), 8877):
        comp = asyncio.run(compare_services(
            _urls([8876, 8877]), paid_tool="market_pulse", task="feed"))
    for c in comp.candidates:
        assert c.usable
        assert c.wake_ms is None
        assert c.woke is False
