"""Tender's own seller-side x402 handshake — a zero-amount 402 on /mcp so the
marketplace validator sees a real challenge, per DECISIONS.md-style reasoning:
free (amount always "0"), but wire-compliant like every other listed ASP.

Unit tests exercise LocalZeroFeeFacilitator directly (fast, no server). The
one HTTP-level test drives the real FastAPI app end to end: unpaid -> 402,
signed zero-value replay -> 200, matching how a real x402 v2 client behaves.
"""
from __future__ import annotations

import asyncio
import secrets
import time

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_response_header,
    encode_payment_signature_header,
)
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
from x402.mechanisms.evm.types import ExactEIP3009Authorization
from x402.schemas import PaymentPayload, PaymentRequirements

from preflight.x402_seller import LocalZeroFeeFacilitator

KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
PAY_TO = "0xAB213fEcccEd629E48A8a4f54C0B636B5ae01eEc"
ASSET = "0x779ded0c9e1022225f8e0630b35a9b54be713736"
NETWORK = "eip155:196"


def _requirements(**overrides) -> PaymentRequirements:
    base = dict(scheme="exact", network=NETWORK, asset=ASSET, amount="0", pay_to=PAY_TO,
                max_timeout_seconds=300, extra={"name": "USD₮0", "version": "1"})
    base.update(overrides)
    return PaymentRequirements(**base)


def _domain_dict(domain) -> dict:
    d = {"name": getattr(domain, "name", None), "version": getattr(domain, "version", None),
         "chainId": getattr(domain, "chain_id", None) or getattr(domain, "chainId", None),
         "verifyingContract": getattr(domain, "verifying_contract", None)
         or getattr(domain, "verifyingContract", None)}
    return {k: v for k, v in d.items() if v is not None}


def _sign(req: PaymentRequirements, *, key: str = KEY, value: str | None = None,
          to: str | None = None, nonce: str | None = None,
          valid_after: int | None = None, valid_before: int | None = None) -> PaymentPayload:
    now = int(time.time())
    auth = ExactEIP3009Authorization(
        from_address=Account.from_key(key).address,
        to=to or req.pay_to,
        value=value if value is not None else req.amount,
        valid_after=str(now - 60 if valid_after is None else valid_after),
        valid_before=str(now + req.max_timeout_seconds if valid_before is None else valid_before),
        # Fresh per call by default: preflight.app's LocalZeroFeeFacilitator is
        # a module-level singleton whose nonce ledger persists for the whole
        # pytest session, so any two tests that both reach settle() with the
        # same nonce would collide ("nonce already used") — a fixed constant
        # here would be a footgun the moment a second caller settles.
        nonce=nonce or ("0x" + secrets.token_hex(32)),
    )
    extra = req.extra or {}
    domain, types, _pt, message = build_typed_data_for_signing(
        auth, 196, req.asset, extra["name"], extra.get("version", "1"))
    signable = encode_typed_data(domain_data=_domain_dict(domain), message_types=types,
                                 message_data=message)
    sig = Account.sign_message(signable, private_key=key)
    return PaymentPayload(
        payload={"authorization": {
            "from": auth.from_address, "to": auth.to, "value": auth.value,
            "validAfter": auth.valid_after, "validBefore": auth.valid_before,
            "nonce": auth.nonce,
        }, "signature": sig.signature.to_0x_hex()},
        accepted=req,
    )


def test_valid_zero_amount_authorization_verifies():
    req = _requirements()
    payload = _sign(req)
    result = asyncio.run(LocalZeroFeeFacilitator(network=NETWORK).verify(payload, req))
    assert result.is_valid, result.invalid_reason
    assert result.payer.lower() == Account.from_key(KEY).address.lower()


def test_settle_is_a_noop_for_zero_amount():
    req = _requirements()
    payload = _sign(req)
    fac = LocalZeroFeeFacilitator(network=NETWORK)
    assert asyncio.run(fac.verify(payload, req)).is_valid
    settle = asyncio.run(fac.settle(payload, req))
    assert settle.success
    assert settle.transaction == ""  # nothing was ever transferred


def test_nonzero_amount_settlement_refused():
    req = _requirements(amount="1000000")
    payload = _sign(req, value="1000000")
    fac = LocalZeroFeeFacilitator(network=NETWORK)
    assert asyncio.run(fac.verify(payload, req)).is_valid
    settle = asyncio.run(fac.settle(payload, req))
    assert not settle.success
    assert "non_zero" in settle.error_reason


def test_tampered_signature_rejected():
    # Flip a hex digit inside the 'r' component (not the trailing v byte,
    # which only ever has two valid values and can recover the same address
    # under either — a weak, flaky tamper). A mid-signature bit flip reliably
    # either fails recovery outright or recovers an address that won't match
    # the claimed `from`.
    req = _requirements()
    payload = _sign(req)
    sig = payload.payload["signature"]
    flipped_char = "0" if sig[10] != "0" else "1"
    tampered_sig = sig[:10] + flipped_char + sig[11:]
    payload.payload["signature"] = tampered_sig
    result = asyncio.run(LocalZeroFeeFacilitator(network=NETWORK).verify(payload, req))
    assert not result.is_valid


def test_wrong_recipient_rejected():
    req = _requirements()
    payload = _sign(req, to="0x000000000000000000000000000000000000dEaD")
    result = asyncio.run(LocalZeroFeeFacilitator(network=NETWORK).verify(payload, req))
    assert not result.is_valid and "payTo" in result.invalid_reason


def test_expired_window_rejected():
    req = _requirements()
    now = int(time.time())
    payload = _sign(req, valid_after=now - 1000, valid_before=now - 500)
    result = asyncio.run(LocalZeroFeeFacilitator(network=NETWORK).verify(payload, req))
    assert not result.is_valid and "expired" in result.invalid_reason


def test_nonce_replay_rejected_on_reverify():
    req = _requirements()
    payload = _sign(req)
    fac = LocalZeroFeeFacilitator(network=NETWORK)
    first = asyncio.run(fac.verify(payload, req))
    assert first.is_valid
    asyncio.run(fac.settle(payload, req))  # marks the nonce used
    second = asyncio.run(fac.verify(payload, req))
    assert not second.is_valid and "nonce" in second.invalid_reason


class TestMcpEndpointHandshake:
    """Drives the real Tender app end to end over HTTP."""

    def test_unpaid_call_gets_a_correct_zero_amount_402(self, server_factory):
        """Only tools/call is paywalled (initialize/tools/list/etc. are free
        — see TestHandshakeIsFree below), so the "requires payment" example
        here has to be an actual tool invocation, not tools/list."""
        from preflight.app import app

        with server_factory(app, 8991):
            resp = httpx.post(
                "http://127.0.0.1:8991/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "get_report", "arguments": {"report_id": "nope"}}},
            )
        assert resp.status_code == 402
        header = resp.headers.get("payment-required")
        assert header
        challenge = decode_payment_required_header(header)
        assert challenge.x402_version == 2
        req = challenge.accepts[0]
        assert req.amount == "0"
        assert req.scheme == "exact"
        assert req.network == NETWORK
        assert req.asset.lower() == ASSET.lower()
        assert req.pay_to == PAY_TO
        assert req.extra.get("name") == "USD₮0"

    def test_signed_zero_value_replay_is_accepted(self, server_factory):
        from preflight.app import app

        with server_factory(app, 8992):
            unpaid = httpx.post(
                "http://127.0.0.1:8992/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "get_report", "arguments": {"report_id": "nope"}}},
            )
            assert unpaid.status_code == 402
            req = decode_payment_required_header(unpaid.headers["payment-required"]).accepts[0]

            payload = _sign(req)
            paid = httpx.post(
                "http://127.0.0.1:8992/mcp/", timeout=15.0,
                headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "PAYMENT-SIGNATURE": encode_payment_signature_header(payload)},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "get_report", "arguments": {"report_id": "nope"}}},
            )
        assert paid.status_code == 200
        body = paid.json()
        assert body["result"]["isError"] is False
        assert "No report with id" in body["result"]["content"][0]["text"]
        settle_header = paid.headers.get("payment-response")
        assert settle_header
        assert decode_payment_response_header(settle_header).success

    def test_bare_get_also_gets_the_402_challenge(self, server_factory):
        """The marketplace's x402 validator probes with a bare GET, not just
        POST, and rejects any ASP that doesn't 402 on it — TRACE (#7515) gates
        every verb, so Tender's gate must too, not just the JSON-RPC POST path."""
        from preflight.app import app

        with server_factory(app, 8993):
            resp = httpx.get("http://127.0.0.1:8993/mcp/", timeout=15.0)
        assert resp.status_code == 402
        header = resp.headers.get("payment-required")
        assert header
        req = decode_payment_required_header(header).accepts[0]
        assert req.amount == "0"
        assert req.scheme == "exact"
        assert req.network == NETWORK
        assert req.pay_to == PAY_TO

    def test_signed_get_replay_is_accepted(self, server_factory):
        """GET carries no JSON-RPC body/method to fulfill, but the
        signed-replay -> 200 flow still has to hold for GET, same as POST:
        once payment verifies, the call succeeds instead of erroring out."""
        from preflight.app import app

        with server_factory(app, 8994):
            unpaid = httpx.get("http://127.0.0.1:8994/mcp/", timeout=15.0)
            assert unpaid.status_code == 402
            req = decode_payment_required_header(unpaid.headers["payment-required"]).accepts[0]

            payload = _sign(req)
            paid = httpx.get(
                "http://127.0.0.1:8994/mcp/", timeout=15.0,
                headers={"PAYMENT-SIGNATURE": encode_payment_signature_header(payload)},
            )
        assert paid.status_code == 200
        assert paid.json()["ok"] is True
        settle_header = paid.headers.get("payment-response")
        assert settle_header
        assert decode_payment_response_header(settle_header).success
