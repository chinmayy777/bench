"""Buyer-side payment engine with hard cash-free guardrails.

Modes:
  off     — never signs anything (C6/C7 skip)
  mock    — signs real EIP-3009 payloads for the offline mock network
  testnet — signs for base-sepolia (faucet funds; settlement by the target's facilitator)

Mainnet networks are refused unconditionally, in code, regardless of mode —
with exactly one deliberate, opt-in exception: X Layer (eip155:196), handled
entirely separately in `_pay_xlayer` below (its own flag, its own key, its
own hard caps). Every other mainnet stays cash-free no matter what.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from x402.schemas.v1 import PaymentRequirementsV1

from .config import MAINNET_NETWORKS, XLAYER_NETWORK, XLAYER_USDT0_ASSET, settings
from .x402kit import SignedPayment, sign_exact_payment, units_to_usdt

log = logging.getLogger("preflight.payer")


class PayerRefused(Exception):
    """Raised when policy forbids paying. Message is user-facing."""


@dataclass
class SpendLedger:
    """In-process spend accounting. `day` resets automatically at UTC midnight;
    a caller tracking a narrower window (e.g. one comparison run) just never
    lets `day` roll over — see Payer.xlayer_run_ledger below."""

    day: str = ""
    spent_usdt: float = 0.0

    def add(self, amount: float) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.spent_usdt = today, 0.0
        self.spent_usdt += amount


# Process-wide singleton so the X Layer daily cap accumulates across separate
# compare_services()/preflight_run() calls, not just within one Payer()
# instance (bench.py constructs a fresh Payer() per run by default). This is
# in-memory only — it resets on a process restart, same as every other cap
# here; there is no cross-process/persistent ledger in this codebase yet.
_xlayer_daily_ledger = SpendLedger()


class Payer:
    def __init__(self, mode: str | None = None, private_key: str | None = None) -> None:
        self.mode = (mode or settings.payer_mode).lower()
        self._key = private_key or settings.payer_private_key
        self.ledger = SpendLedger()
        # Per-run X Layer spend — a fresh Payer() per compare_services() call
        # (the default) makes this naturally scoped to one comparison run.
        self.xlayer_run_ledger = SpendLedger()

    @property
    def enabled(self) -> bool:
        return self.mode in ("mock", "testnet") and not settings.kill_switch and bool(self._key)

    def refusal_reason(self) -> str:
        if settings.kill_switch:
            return "payer kill switch is on"
        if self.mode == "off":
            return "payer disabled (PAYER_MODE=off)"
        if not self._key:
            return "no PAYER_PRIVATE_KEY configured"
        return ""

    def pay(self, req: PaymentRequirementsV1) -> SignedPayment:
        network = (req.network or "").lower()
        if network == XLAYER_NETWORK:
            return self._pay_xlayer(req)
        if not self.enabled:
            raise PayerRefused(self.refusal_reason())
        if network in MAINNET_NETWORKS:
            raise PayerRefused(
                f"target quotes mainnet network {req.network!r}; "
                "mainnet spending is disabled by operator policy (cash-free mode)"
            )
        if network not in [n.lower() for n in settings.allowed_pay_networks]:
            raise PayerRefused(f"network {req.network!r} not in the allowed pay list")
        amount = units_to_usdt(req.max_amount_required)
        if amount > settings.max_pay_per_call_usdt:
            raise PayerRefused(
                f"quoted {amount} exceeds per-call cap {settings.max_pay_per_call_usdt}"
            )
        if self.ledger.spent_usdt + amount > settings.max_pay_per_day_usdt:
            raise PayerRefused("daily spend cap reached")
        signed = sign_exact_payment(self._key, req)
        self.ledger.add(amount)
        return signed

    def _pay_xlayer(self, req: PaymentRequirementsV1) -> SignedPayment:
        """X Layer (eip155:196) real mainnet money. Every gate here refuses
        with a specific, user-facing reason — never a silent skip — per the
        operator's explicit request that a capped-out or disabled purchase be
        loud, not quiet."""
        if not settings.xlayer_spending_enabled:
            raise PayerRefused(
                "X Layer mainnet spending is disabled (set XLAYER_SPENDING_ENABLED=true "
                "to enable it — refused by default)"
            )
        if settings.kill_switch:
            raise PayerRefused("payer kill switch is on")
        if not settings.xlayer_payer_private_key:
            raise PayerRefused("no XLAYER_PAYER_PRIVATE_KEY configured")
        asset = (req.asset or "").lower()
        if asset != XLAYER_USDT0_ASSET.lower():
            raise PayerRefused(
                f"X Layer spending only supports the USD₮0 asset {XLAYER_USDT0_ASSET}; "
                f"quoted asset {req.asset!r} is not recognized"
            )
        amount = units_to_usdt(req.max_amount_required)
        if amount > settings.max_xlayer_pay_per_call_usdt:
            raise PayerRefused(
                f"quoted {amount} USD₮0 exceeds X Layer per-call cap "
                f"{settings.max_xlayer_pay_per_call_usdt}"
            )
        if self.xlayer_run_ledger.spent_usdt + amount > settings.max_xlayer_pay_per_run_usdt:
            raise PayerRefused(
                f"would exceed X Layer per-run cap {settings.max_xlayer_pay_per_run_usdt} USD₮0 "
                f"(already spent {self.xlayer_run_ledger.spent_usdt} USD₮0 this run)"
            )
        if _xlayer_daily_ledger.spent_usdt + amount > settings.max_xlayer_pay_per_day_usdt:
            raise PayerRefused(
                f"would exceed X Layer per-day cap {settings.max_xlayer_pay_per_day_usdt} USD₮0 "
                f"(already spent {_xlayer_daily_ledger.spent_usdt} USD₮0 today)"
            )
        log.warning(
            "XLAYER mainnet settlement attempt: amount=%s asset=%s target=%s network=%s",
            amount, req.asset, req.pay_to, req.network,
        )
        signed = sign_exact_payment(settings.xlayer_payer_private_key, req)
        self.xlayer_run_ledger.add(amount)
        _xlayer_daily_ledger.add(amount)
        return signed
