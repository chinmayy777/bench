"""x402 building blocks shared by the payer, the checks, and the demo fixture.

Uses OKX's official x402 SDK schemas and EIP-712 builder so every payload we
produce or verify is byte-compatible with the real marketplace flow. The
MockFacilitator gives cash-free, offline settlement verification with REAL
signature recovery — the same cryptography, minus the chain write.
"""
from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
from x402.mechanisms.evm.types import ExactEIP3009Authorization
from x402.schemas.v1 import PaymentRequiredV1, PaymentRequirementsV1

USDT_DECIMALS = 6
SCHEME = "exact"

# Networks the kit knows how to sign for. "mock" is our offline network for
# local tests and the judge-safe demo; base-sepolia is the SDK's blessed
# testnet (free faucet USDC) for real on-chain settlement at zero cost.
KNOWN_NETWORKS: dict[str, dict[str, Any]] = {
    "mock": {"chain_id": 31337, "asset": "0x" + "11" * 20, "name": "MockUSD", "version": "1"},
    "base-sepolia": {
        "chain_id": 84532,
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "name": "USDC",
        "version": "2",
    },
}


def usdt_to_units(amount: float) -> str:
    return str(int(round(amount * 10**USDT_DECIMALS)))


def units_to_usdt(units: str) -> float:
    return int(units) / 10**USDT_DECIMALS


def build_challenge(
    *,
    pay_to: str,
    amount_usdt: float,
    network: str,
    resource: str,
    description: str,
) -> dict:
    """Server-side: construct a v1 402 body via SDK schemas (camelCase wire form)."""
    net = KNOWN_NETWORKS[network]
    req = PaymentRequirementsV1(
        scheme=SCHEME,
        network=network,
        max_amount_required=usdt_to_units(amount_usdt),
        resource=resource,
        description=description,
        mime_type="application/json",
        pay_to=pay_to,
        max_timeout_seconds=120,
        asset=net["asset"],
        extra={"name": net["name"], "version": net["version"], "chainId": net["chain_id"]},
    )
    body = PaymentRequiredV1(error="payment required", accepts=[req])
    return body.model_dump(by_alias=True, exclude_none=True)


def parse_challenge(data: dict) -> PaymentRequiredV1:
    """Validate a 402 body against the official schema. Raises on malformed input."""
    return PaymentRequiredV1.model_validate(data)


@dataclass
class SignedPayment:
    header_value: str  # base64 X-PAYMENT header
    payload: dict
    from_address: str
    nonce: str
    amount_units: str
    network: str


def sign_exact_payment(private_key: str, req: PaymentRequirementsV1) -> SignedPayment:
    """Client-side: sign an EIP-3009 TransferWithAuthorization for the given requirement."""
    acct = Account.from_key(private_key)
    now = int(time.time())
    extra = req.extra or {}
    net = KNOWN_NETWORKS.get(req.network, {})
    chain_id = int(extra.get("chainId") or net.get("chain_id"))
    token_name = extra.get("name") or net.get("name")
    token_version = extra.get("version") or net.get("version", "1")

    auth = ExactEIP3009Authorization(
        from_address=acct.address,
        to=req.pay_to,
        value=req.max_amount_required,
        valid_after=str(now - 60),
        valid_before=str(now + req.max_timeout_seconds),
        nonce="0x" + secrets.token_hex(32),
    )
    domain, types, primary_type, message = build_typed_data_for_signing(
        auth, chain_id, req.asset, token_name, token_version
    )
    signable = encode_typed_data(
        domain_data=_domain_dict(domain), message_types=types, message_data=message
    )
    sig = Account.sign_message(signable, private_key=private_key)

    payload = {
        "x402Version": 1,
        "scheme": req.scheme,
        "network": req.network,
        "payload": {
            "signature": sig.signature.to_0x_hex(),
            "authorization": {
                "from": auth.from_address,
                "to": auth.to,
                "value": auth.value,
                "validAfter": auth.valid_after,
                "validBefore": auth.valid_before,
                "nonce": auth.nonce,
            },
        },
    }
    header = base64.b64encode(json.dumps(payload).encode()).decode()
    return SignedPayment(
        header_value=header,
        payload=payload,
        from_address=acct.address,
        nonce=auth.nonce,
        amount_units=req.max_amount_required,
        network=req.network,
    )


def _domain_dict(domain: Any) -> dict:
    d = {
        "name": getattr(domain, "name", None),
        "version": getattr(domain, "version", None),
        "chainId": getattr(domain, "chain_id", None) or getattr(domain, "chainId", None),
        "verifyingContract": getattr(domain, "verifying_contract", None)
        or getattr(domain, "verifyingContract", None),
    }
    return {k: v for k, v in d.items() if v is not None}


class MockFacilitator:
    """Offline verify+settle: real signature recovery, in-memory nonce ledger.

    This is what makes the whole golden path testable with zero cash and zero
    network. verify() returns (ok, payer_address_or_reason, tx_ref).
    """

    def __init__(self) -> None:
        self._used_nonces: set[str] = set()

    def verify(self, header_value: str, req: PaymentRequirementsV1) -> tuple[bool, str, str]:
        try:
            payload = json.loads(base64.b64decode(header_value))
        except Exception:
            return False, "X-PAYMENT header is not base64 JSON", ""
        try:
            if payload.get("scheme") != req.scheme or payload.get("network") != req.network:
                return False, "scheme/network mismatch", ""
            inner = payload["payload"]
            auth = inner["authorization"]
            if auth["to"].lower() != req.pay_to.lower():
                return False, "payTo mismatch", ""
            if int(auth["value"]) < int(req.max_amount_required):
                return False, "amount below required", ""
            now = int(time.time())
            if not (int(auth["validAfter"]) <= now <= int(auth["validBefore"])):
                return False, "authorization expired or not yet valid", ""
            if auth["nonce"] in self._used_nonces:
                return False, "nonce already used", ""
            extra = req.extra or {}
            a = ExactEIP3009Authorization(
                from_address=auth["from"],
                to=auth["to"],
                value=auth["value"],
                valid_after=auth["validAfter"],
                valid_before=auth["validBefore"],
                nonce=auth["nonce"],
            )
            domain, types, _pt, message = build_typed_data_for_signing(
                a,
                int(extra.get("chainId", 31337)),
                req.asset,
                extra.get("name", "MockUSD"),
                extra.get("version", "1"),
            )
            signable = encode_typed_data(
                domain_data=_domain_dict(domain), message_types=types, message_data=message
            )
            recovered = Account.recover_message(signable, signature=inner["signature"])
            if recovered.lower() != auth["from"].lower():
                return False, "signature does not match payer address", ""
            self._used_nonces.add(auth["nonce"])
            tx_ref = "mock:" + auth["nonce"][2:18]
            return True, recovered, tx_ref
        except (KeyError, ValueError, TypeError) as e:
            return False, f"malformed payment payload: {e}", ""
