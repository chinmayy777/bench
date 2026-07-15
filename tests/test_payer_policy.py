import pytest
from preflight.payer import Payer, PayerRefused
from preflight.x402kit import build_challenge, parse_challenge

KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"


def _req(network="mock", amount=0.05):
    body = build_challenge(pay_to="0x2f7cF9d979A98d0C4Cd2c92c8DC0d9DFf4a04d2A",
                           amount_usdt=amount, network=network,
                           resource="http://t/", description="t")
    return parse_challenge(body).accepts[0]


def test_mainnet_always_refused():
    req = _req()
    req.network = "eip155:196"
    with pytest.raises(PayerRefused, match="mainnet"):
        Payer(mode="mock", private_key=KEY).pay(req)


def test_per_call_cap():
    with pytest.raises(PayerRefused, match="cap"):
        Payer(mode="mock", private_key=KEY).pay(_req(amount=5.0))


def test_off_mode_refuses():
    with pytest.raises(PayerRefused):
        Payer(mode="off", private_key=KEY).pay(_req())
