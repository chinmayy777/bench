"""Seller-side x402 facilitator for Tender's own (currently zero-fee) A2MCP endpoint.

This is deliberately NOT x402.http.OKXFacilitatorClient. That client's
get_supported() is an authenticated call to OKX's hosted facilitator
(api/v6/pay/x402/supported), and x402ResourceServer.build_payment_requirements()
refuses to run at all until initialize() -> get_supported() has succeeded — so
the hosted client would require real OKX facilitator credentials just to issue
the unpaid 402, not only to verify a real replay.

LocalZeroFeeFacilitator instead satisfies x402's FacilitatorClient protocol
entirely in-process: get_supported() is declared locally (no network call, no
credentials), and verify() performs real EIP-712/EIP-3009 signature recovery —
the same typed-data builder x402kit.py already uses to SIGN payments, used here
to check one. settle() is a genuine no-op when the requirement's amount is "0"
(there is nothing to transfer), which is the only amount this service charges
today.

Extending settle() to broadcast a real (non-zero) on-chain transfer, or
swapping in OKXFacilitatorClient for hosted verify/settle, is future work for
if/when a Tender tool is ever priced above zero — not implemented here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from eth_account import Account
from eth_account.messages import encode_typed_data
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
from x402.mechanisms.evm.types import ExactEIP3009Authorization
from x402.mechanisms.evm.utils import get_evm_chain_id
from x402.schemas import PaymentPayload, PaymentRequirements, SettleResponse, SupportedResponse
from x402.schemas.responses import SupportedKind, VerifyResponse

SCHEME_EXACT = "exact"


def _domain_dict(domain) -> dict:
    d = {
        "name": getattr(domain, "name", None),
        "version": getattr(domain, "version", None),
        "chainId": getattr(domain, "chain_id", None) or getattr(domain, "chainId", None),
        "verifyingContract": getattr(domain, "verifying_contract", None)
        or getattr(domain, "verifyingContract", None),
    }
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class LocalZeroFeeFacilitator:
    """FacilitatorClient for a single (scheme, network) pair, priced at zero.

    Holds its own nonce ledger (in-memory, per-process) for replay protection.
    A restart clears it — acceptable here since nothing of value is at stake
    for a $0 authorization; this is not the ledger a paid tool would need.
    """

    network: str
    _used_nonces: set[str] = field(default_factory=set)

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme=SCHEME_EXACT, network=self.network)]
        )

    async def verify_signature(
        self, payload: PaymentPayload, requirements: PaymentRequirements | None = None
    ) -> VerifyResponse:
        if requirements is None:
            return VerifyResponse(is_valid=False, invalid_reason="no requirements to check against")
        return await self.verify(payload, requirements)

    async def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse:
        try:
            auth_data = payload.payload["authorization"]
            signature = payload.payload["signature"]
        except (KeyError, TypeError):
            return VerifyResponse(is_valid=False, invalid_reason="malformed payment payload")

        if requirements.pay_to.lower() != auth_data.get("to", "").lower():
            return VerifyResponse(is_valid=False, invalid_reason="payTo mismatch")
        if int(auth_data.get("value", -1)) < int(requirements.amount):
            return VerifyResponse(is_valid=False, invalid_reason="amount below required")

        now = int(time.time())
        try:
            valid_after = int(auth_data["validAfter"])
            valid_before = int(auth_data["validBefore"])
        except (KeyError, TypeError, ValueError):
            return VerifyResponse(is_valid=False, invalid_reason="malformed validity window")
        if not (valid_after <= now <= valid_before):
            return VerifyResponse(is_valid=False, invalid_reason="authorization expired or not yet valid")

        nonce = auth_data.get("nonce", "")
        if nonce in self._used_nonces:
            return VerifyResponse(is_valid=False, invalid_reason="nonce already used")

        try:
            chain_id = get_evm_chain_id(requirements.network)
            extra = requirements.extra or {}
            token_name = extra.get("name")
            token_version = extra.get("version", "1")
            if not token_name:
                return VerifyResponse(is_valid=False, invalid_reason="missing EIP-712 token name in requirements.extra")

            auth = ExactEIP3009Authorization(
                from_address=auth_data.get("from", ""),
                to=auth_data["to"],
                value=str(auth_data["value"]),
                valid_after=str(valid_after),
                valid_before=str(valid_before),
                nonce=nonce,
            )
            domain, types, _primary_type, message = build_typed_data_for_signing(
                auth, chain_id, requirements.asset, token_name, token_version
            )
            signable = encode_typed_data(
                domain_data=_domain_dict(domain), message_types=types, message_data=message
            )
            recovered = Account.recover_message(signable, signature=signature)
        except Exception as e:  # malformed signature / typed-data — fail closed, don't crash
            return VerifyResponse(is_valid=False, invalid_reason=f"signature recovery failed: {e}")

        if recovered.lower() != auth_data.get("from", "").lower():
            return VerifyResponse(is_valid=False, invalid_reason="signature does not match payer address")

        return VerifyResponse(is_valid=True, payer=recovered)

    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        if requirements.amount != "0":
            return SettleResponse(
                success=False,
                error_reason="non_zero_settlement_not_implemented",
                error_message=(
                    "This facilitator only settles zero-amount authorizations; "
                    "real on-chain settlement for a priced tool is not implemented."
                ),
                transaction="",
                network=requirements.network,
            )
        # Nothing to transfer — mark the nonce used (so the same authorization
        # can't be replayed to bypass a future non-zero price) and succeed.
        nonce = (payload.payload or {}).get("authorization", {}).get("nonce", "")
        if nonce:
            self._used_nonces.add(nonce)
        payer = (payload.payload or {}).get("authorization", {}).get("from")
        return SettleResponse(success=True, status="success", payer=payer,
                              transaction="", network=requirements.network)
