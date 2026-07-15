import time
from preflight.x402kit import (MockFacilitator, build_challenge, parse_challenge,
                               sign_exact_payment)

KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
PAY_TO = "0x2f7cF9d979A98d0C4Cd2c92c8DC0d9DFf4a04d2A"


def _challenge(amount=0.05):
    body = build_challenge(pay_to=PAY_TO, amount_usdt=amount, network="mock",
                           resource="http://t/mcp/", description="test")
    return parse_challenge(body).accepts[0]


def test_challenge_wire_shape_is_camel_case():
    body = build_challenge(pay_to=PAY_TO, amount_usdt=0.05, network="mock",
                           resource="http://t/mcp/", description="test")
    assert body["x402Version"] == 1
    req = body["accepts"][0]
    assert req["maxAmountRequired"] == "50000"
    assert req["payTo"] == PAY_TO


def test_sign_and_verify_roundtrip():
    req = _challenge()
    signed = sign_exact_payment(KEY, req)
    fac = MockFacilitator()
    ok, who, tx = fac.verify(signed.header_value, req)
    assert ok, who
    assert who.lower() == signed.from_address.lower()
    assert tx.startswith("mock:")


def test_nonce_replay_rejected():
    req = _challenge()
    signed = sign_exact_payment(KEY, req)
    fac = MockFacilitator()
    assert fac.verify(signed.header_value, req)[0]
    ok, reason, _ = fac.verify(signed.header_value, req)
    assert not ok and "nonce" in reason


def test_underpayment_rejected():
    cheap = _challenge(amount=0.01)
    signed = sign_exact_payment(KEY, cheap)
    expensive = _challenge(amount=0.25)
    ok, reason, _ = MockFacilitator().verify(signed.header_value, expensive)
    assert not ok and "amount" in reason


def test_wrong_recipient_rejected():
    req = _challenge()
    signed = sign_exact_payment(KEY, req)
    other = _challenge()
    other.pay_to = "0x000000000000000000000000000000000000dEaD"
    ok, reason, _ = MockFacilitator().verify(signed.header_value, other)
    assert not ok and "payTo" in reason
