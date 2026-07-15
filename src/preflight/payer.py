"""Buyer-side payment engine with hard cash-free guardrails.

Modes:
  off     — never signs anything (C6/C7 skip)
  mock    — signs real EIP-3009 payloads for the offline mock network
  testnet — signs for base-sepolia (faucet funds; settlement by the target's facilitator)

Mainnet networks are refused unconditionally, in code, regardless of mode.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from x402.schemas.v1 import PaymentRequirementsV1

from .config import MAINNET_NETWORKS, settings
from .x402kit import SignedPayment, sign_exact_payment, units_to_usdt


class PayerRefused(Exception):
    """Raised when policy forbids paying. Message is user-facing."""


@dataclass
class SpendLedger:
    """In-process daily spend accounting (persisted spend lives in the report store)."""

    day: str = ""
    spent_usdt: float = 0.0

    def add(self, amount: float) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.spent_usdt = today, 0.0
        self.spent_usdt += amount


class Payer:
    def __init__(self, mode: str | None = None, private_key: str | None = None) -> None:
        self.mode = (mode or settings.payer_mode).lower()
        self._key = private_key or settings.payer_private_key
        self.ledger = SpendLedger()

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
        if not self.enabled:
            raise PayerRefused(self.refusal_reason())
        network = (req.network or "").lower()
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
