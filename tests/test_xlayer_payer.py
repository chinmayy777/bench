"""X Layer (eip155:196) mainnet spending: opt-in flag, dedicated key, hard
caps, asset allowlist, and WARNING-level logging on every settlement attempt.

Every other mainnet must stay unconditionally refused regardless of this
flag — that's the one thing that must never regress here.
"""
from __future__ import annotations

import dataclasses
import logging

import pytest

from preflight import config as config_module
from preflight import payer as payer_module
from preflight.payer import Payer, PayerRefused
from preflight.x402_probe import NormalizedRequirement

KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
PAY_TO = "0x00dC0f3ff1F2bca6b3d007684cC25a766c9815f4"
USDT0 = "0x779ded0c9e1022225f8e0630b35a9b54be713736"


def _req(amount_units="500000", asset=USDT0, network="eip155:196"):
    return NormalizedRequirement(
        scheme="exact", network=network, chain_id=196, amount_units=amount_units,
        asset=asset, pay_to=PAY_TO, max_timeout_seconds=300,
        extra={"name": "USD₮0", "version": "1"})


def _patch_settings(monkeypatch, **overrides):
    new = dataclasses.replace(payer_module.settings, **overrides)
    monkeypatch.setattr(payer_module, "settings", new)
    monkeypatch.setattr(config_module, "settings", new)
    return new


def test_disabled_by_default_even_with_key_configured(monkeypatch):
    """The flag, not merely having a key, is the gate — a key alone must
    never be enough to spend real mainnet funds."""
    _patch_settings(monkeypatch, xlayer_spending_enabled=False,
                    xlayer_payer_private_key=KEY)
    with pytest.raises(PayerRefused, match="XLAYER_SPENDING_ENABLED"):
        Payer().pay(_req())


def test_enabled_without_xlayer_key_refuses_even_if_testnet_key_present(monkeypatch):
    """Enabling the flag must not fall back to the testnet/mock
    PAYER_PRIVATE_KEY — X Layer requires its own dedicated key."""
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key="", payer_private_key=KEY)
    with pytest.raises(PayerRefused, match="XLAYER_PAYER_PRIVATE_KEY"):
        Payer(private_key=KEY).pay(_req())


def test_other_mainnets_still_unconditionally_refused_regardless_of_flag(monkeypatch):
    """The whole point: enabling X Layer must never widen the hole to any
    other mainnet."""
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY)
    req = _req(network="eip155:8453")  # base mainnet
    with pytest.raises(PayerRefused, match="mainnet"):
        Payer(mode="mock", private_key=KEY).pay(req)


def test_unrecognized_asset_refused(monkeypatch):
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY)
    req = _req(asset="0xDEADBEEFdeadbeefDEADBEEFdeadbeefDEADBEEF")
    with pytest.raises(PayerRefused, match="USD₮0"):
        Payer().pay(req)


def test_per_call_cap_enforced(monkeypatch):
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY, max_xlayer_pay_per_call_usdt=1.0)
    over_cap = _req(amount_units="2000000")  # 2.0 USD₮0
    with pytest.raises(PayerRefused, match="per-call cap"):
        Payer().pay(over_cap)


def test_per_run_cap_enforced_across_multiple_calls(monkeypatch):
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY,
                    max_xlayer_pay_per_call_usdt=10.0,
                    max_xlayer_pay_per_run_usdt=3.0,
                    max_xlayer_pay_per_day_usdt=100.0)
    p = Payer()
    p.pay(_req(amount_units="1000000"))  # 1.0 -> run total 1.0
    p.pay(_req(amount_units="1000000"))  # 1.0 -> run total 2.0
    p.pay(_req(amount_units="1000000"))  # 1.0 -> run total 3.0, at cap, allowed
    with pytest.raises(PayerRefused, match="per-run cap"):
        p.pay(_req(amount_units="1000000"))  # would bring to 4.0 -> refused


def test_per_run_cap_resets_on_a_new_payer_instance(monkeypatch):
    """bench.py constructs a fresh Payer() per compare_services() call by
    default — the run cap must reset accordingly, not leak across runs."""
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY,
                    max_xlayer_pay_per_call_usdt=10.0,
                    max_xlayer_pay_per_run_usdt=3.0,
                    max_xlayer_pay_per_day_usdt=100.0)
    Payer().pay(_req(amount_units="3000000"))  # exhausts one run's cap
    # A fresh Payer() (new run) must be able to spend again immediately.
    Payer().pay(_req(amount_units="1000000"))


def test_per_day_cap_persists_across_separate_payer_instances(monkeypatch):
    """Unlike the per-run cap, the daily cap is a process-wide singleton
    (_xlayer_daily_ledger) specifically so it accumulates across separate
    compare_services() calls, not just within one."""
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY,
                    max_xlayer_pay_per_call_usdt=100.0,
                    max_xlayer_pay_per_run_usdt=100.0,
                    max_xlayer_pay_per_day_usdt=5.0)
    payer_module._xlayer_daily_ledger.day = ""
    payer_module._xlayer_daily_ledger.spent_usdt = 0.0
    Payer().pay(_req(amount_units="3000000"))  # run 1: 3.0 -> day total 3.0
    Payer().pay(_req(amount_units="1000000"))  # run 2: 1.0 -> day total 4.0
    with pytest.raises(PayerRefused, match="per-day cap"):
        Payer().pay(_req(amount_units="2000000"))  # would bring day total to 6.0


def test_exceeding_a_cap_refuses_with_a_specific_reason_never_silent(monkeypatch):
    """Every refusal path must raise with an actionable, specific message —
    never fail silently or return None/skip without explanation."""
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY, max_xlayer_pay_per_call_usdt=0.01)
    with pytest.raises(PayerRefused) as exc_info:
        Payer().pay(_req(amount_units="500000"))
    msg = str(exc_info.value)
    assert msg and "cap" in msg and "0.01" in msg


def test_valid_payment_signs_and_logs_at_warning(monkeypatch, caplog):
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY,
                    max_xlayer_pay_per_call_usdt=1.0,
                    max_xlayer_pay_per_run_usdt=3.0,
                    max_xlayer_pay_per_day_usdt=10.0)
    payer_module._xlayer_daily_ledger.day = ""
    payer_module._xlayer_daily_ledger.spent_usdt = 0.0
    with caplog.at_level(logging.WARNING, logger="preflight.payer"):
        signed = Payer().pay(_req(amount_units="500000"))
    assert signed.network == "eip155:196"
    assert signed.amount_units == "500000"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "XLAYER" in msg and "0.5" in msg and USDT0 in msg and PAY_TO in msg


def test_kill_switch_blocks_xlayer_spending_too(monkeypatch):
    """Defense in depth: the existing global PAYER_KILL_SWITCH must also stop
    real mainnet spending, not just the mock/testnet payer."""
    _patch_settings(monkeypatch, xlayer_spending_enabled=True,
                    xlayer_payer_private_key=KEY, kill_switch=True)
    with pytest.raises(PayerRefused, match="kill switch"):
        Payer().pay(_req())


def test_xlayer_asset_and_network_known_to_the_signer():
    """eip155:196 / USD₮0 must be registered in x402kit's KNOWN_NETWORKS so
    EIP-712 domain params resolve without relying on a challenge's `extra`."""
    from preflight.x402kit import KNOWN_NETWORKS
    net = KNOWN_NETWORKS["eip155:196"]
    assert net["chain_id"] == 196
    assert net["asset"].lower() == USDT0.lower()
