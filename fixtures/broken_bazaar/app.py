"""BrokenBazaar — the demo fixture PreFlight tests against.

A tiny paid data vendor ("market_pulse") behind a real x402 v1 paywall, with
two intentionally toggleable bugs:

  BUG_PRICE=1  the 402 quotes 0.25 while the listing says 0.05 (overcharge)
  BUG_EMPTY=1  payment settles but the paid tool delivers empty content

Payment verification is pluggable: MockFacilitator (offline; default) or the
OKX facilitator when deployed for real testnet settlement.
"""
from __future__ import annotations

import json
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from preflight.x402kit import MockFacilitator, build_challenge, parse_challenge  # noqa: E402

LISTED_PRICE = 0.05
PAID_TOOL = "market_pulse"
DEFAULT_PAY_TO = "0x2f7cF9d979A98d0C4Cd2c92c8DC0d9DFf4a04d2A"


def _pulse_text() -> str:
    return json.dumps({
        "pulse": "steady",
        "signals": [
            {"pair": "OKB/USDT", "drift_bps": 12, "note": "range-bound"},
            {"pair": "ETH/USDT", "drift_bps": -8, "note": "cooling"},
        ],
        "disclaimer": "demo data — BrokenBazaar is a test fixture",
    })


def build_mcp(bug_empty: bool) -> FastMCP:
    mcp = FastMCP("BrokenBazaar", instructions="Demo paid data vendor for PreFlight.")

    @mcp.tool
    def ping() -> str:
        """Free liveness check."""
        return "pong"

    @mcp.tool
    def market_pulse() -> str:
        """Paid: a tiny synthetic market pulse report (x402-gated)."""
        return "" if bug_empty else _pulse_text()

    return mcp


class PaywallASGI:
    """Pure-ASGI wrapper: gates tools/call for the paid tool behind x402.

    Buffers the request body (so downstream still receives it), returns a
    schema-exact 402 when unpaid, verifies X-PAYMENT via the facilitator when
    paid, and passes through on success.
    """

    def __init__(self, app, *, pay_to: str, network: str, quote_price: float,
                 facilitator, resource: str) -> None:
        self.app = app
        self.pay_to, self.network = pay_to, network
        self.quote_price, self.facilitator = quote_price, facilitator
        self.resource = resource

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["method"] == "GET"
                and scope.get("path", "").rstrip("/") == "/healthz"):
            return await self._json(send, 200, {"ok": True})

        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope.get("path", "").rstrip("/") != "/mcp"):
            return await self.app(scope, receive, send)

        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}

        gated = False
        try:
            data = json.loads(body or b"{}")
            gated = (data.get("method") == "tools/call"
                     and data.get("params", {}).get("name") == PAID_TOOL)
        except json.JSONDecodeError:
            gated = False

        if gated:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            challenge = build_challenge(
                pay_to=self.pay_to, amount_usdt=self.quote_price,
                network=self.network, resource=self.resource,
                description=f"BrokenBazaar {PAID_TOOL}",
            )
            payment = headers.get("x-payment")
            if not payment:
                return await self._json(send, 402, challenge)
            req = parse_challenge(challenge).accepts[0]
            ok, who_or_reason, tx_ref = self.facilitator.verify(payment, req)
            if not ok:
                challenge["error"] = f"payment rejected: {who_or_reason}"
                return await self._json(send, 402, challenge)
            # settled — pass through, exposing a settle reference header
            async def send_with_receipt(message):
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append(
                        (b"x-payment-response", tx_ref.encode()))
                await send(message)
            return await self.app(scope, replay, send_with_receipt)

        return await self.app(scope, replay, send)

    @staticmethod
    async def _json(send, status: int, payload: dict):
        body = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def create_app(*, bug_price: bool = False, bug_empty: bool = False,
               network: str = "mock", pay_to: str = DEFAULT_PAY_TO,
               facilitator=None, resource: str = "http://bazaar.local/mcp/"):
    mcp = build_mcp(bug_empty)
    inner = mcp.http_app(path="/mcp/", stateless_http=True, json_response=True)
    quote = 0.25 if bug_price else LISTED_PRICE
    wrapped = PaywallASGI(inner, pay_to=pay_to, network=network, quote_price=quote,
                          facilitator=facilitator or MockFacilitator(), resource=resource)
    wrapped.lifespan = inner.lifespan  # let servers reuse the MCP lifespan
    wrapped.inner_app = inner
    return wrapped



class DirectSettleFacilitator:
    """Verify an x402 payment and settle it directly on-chain — no OKX API keys.

    Recovers the EIP-3009 signature (same crypto as the mock), then submits the
    signed transferWithAuthorization to the chain via a public RPC, using a
    relayer EOA that pays gas. Returns (ok, payer_or_reason, tx_hash) with a
    REAL, explorer-verifiable transaction hash.

    Env (set on the fixture deployment):
      NETWORK=base-sepolia
      RELAYER_PRIVATE_KEY=0x...   funds gas + submits tx (throwaway testnet key)
      RPC_URL=...                 optional; sensible default per network
    """

    # from constants.py TRANSFER_WITH_AUTHORIZATION_VRS_ABI (SDK-canonical)
    _TWA_ABI = [{
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
            {"name": "v", "type": "uint8"},
            {"name": "r", "type": "bytes32"},
            {"name": "s", "type": "bytes32"},
        ],
        "name": "transferWithAuthorization",
        "outputs": [], "stateMutability": "nonpayable", "type": "function",
    }]

    _DEFAULT_RPC = {
        "base-sepolia": "https://base-sepolia-rpc.publicnode.com",
        "eip155:84532": "https://base-sepolia-rpc.publicnode.com",
    }

    def __init__(self, network: str, relayer_key: str,
                 usdc_address: str, chain_id: int, rpc_url: str | None = None) -> None:
        from web3 import Web3
        rpc = rpc_url or self._DEFAULT_RPC.get(network)
        if not rpc:
            raise ValueError(f"no RPC configured for network {network!r}")
        if not relayer_key:
            raise ValueError("RELAYER_PRIVATE_KEY is required for direct settlement")
        from eth_account import Account
        self._w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
        self._relayer = Account.from_key(relayer_key)
        self._chain_id = chain_id
        self._usdc = Web3.to_checksum_address(usdc_address)
        self._contract = self._w3.eth.contract(address=self._usdc, abi=self._TWA_ABI)
        self._used_nonces: set[str] = set()
        # reuse the mock's verification logic for signature + field checks
        self._verifier = MockFacilitator()

    def verify(self, header_value: str, req) -> tuple[bool, str, str]:
        import base64 as b64
        import json as _json

        # 1. Cryptographic + field verification (offline, exact same as mock path).
        ok, who_or_reason, _ = self._verifier.verify(header_value, req)
        if not ok:
            return False, who_or_reason, ""
        payer = who_or_reason

        # 2. Settle on-chain: submit the signed authorization.
        try:
            payload = _json.loads(b64.b64decode(header_value))
            inner = payload["payload"]
            auth = inner["authorization"]
            sig = inner["signature"]
            if sig.startswith("0x"):
                sig = sig[2:]
            raw = bytes.fromhex(sig)
            if len(raw) != 65:
                return False, f"signature must be 65 bytes, got {len(raw)}", ""
            r = raw[0:32]
            s = raw[32:64]
            v = raw[64]
            if v < 27:
                v += 27
            nonce_bytes = bytes.fromhex(auth["nonce"][2:])

            from web3 import Web3
            fn = self._contract.functions.transferWithAuthorization(
                Web3.to_checksum_address(auth["from"]),
                Web3.to_checksum_address(auth["to"]),
                int(auth["value"]),
                int(auth["validAfter"]),
                int(auth["validBefore"]),
                nonce_bytes, v, r, s,
            )
            tx = fn.build_transaction({
                "from": self._relayer.address,
                "nonce": self._w3.eth.get_transaction_count(self._relayer.address),
                "chainId": self._chain_id,
                # set fees explicitly; some public RPCs lack eth_maxPriorityFeePerGas
                "maxFeePerGas": self._w3.to_wei(2, "gwei"),
                "maxPriorityFeePerGas": self._w3.to_wei(1, "gwei"),
            })
            # est. gas w/ headroom; publicnode sometimes underestimates 3009
            try:
                tx["gas"] = int(self._w3.eth.estimate_gas(tx) * 1.3)
            except Exception:
                tx["gas"] = 120_000
            signed = self._relayer.sign_transaction(tx)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status != 1:
                return False, f"on-chain settlement reverted (tx {tx_hash.hex()})", ""
            txh = tx_hash.hex()
            if not txh.startswith("0x"):
                txh = "0x" + txh
            ref = f"base-sepolia:{txh}"
            return True, payer, ref
        except Exception as e:  # RPC down, out of gas, nonce race, etc.
            return False, f"settlement error: {type(e).__name__}: {e}", ""


def env_app():
    flag = lambda n: os.getenv(n, "0").lower() in {"1", "true", "yes"}
    network = os.getenv("NETWORK", "mock")
    mode = os.getenv("FACILITATOR", "mock")
    fac = None
    if mode == "direct":
        from preflight.x402kit import KNOWN_NETWORKS
        net = KNOWN_NETWORKS[network]
        fac = DirectSettleFacilitator(
            network=network,
            relayer_key=os.getenv("RELAYER_PRIVATE_KEY", ""),
            usdc_address=net["asset"],
            chain_id=net["chain_id"],
            rpc_url=os.getenv("RPC_URL") or None,
        )
    return create_app(
        bug_price=flag("BUG_PRICE"), bug_empty=flag("BUG_EMPTY"),
        network=network,
        pay_to=os.getenv("PAY_TO", DEFAULT_PAY_TO),
        facilitator=fac,
        resource=os.getenv("RESOURCE_URL", "http://bazaar.local/mcp/"),
    )


app = env_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8801")))
