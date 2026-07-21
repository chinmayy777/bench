"""Version-agnostic, lenient x402 402-challenge fetching and parsing.

Real marketplace ASPs disagree on almost everything except the wire
essentials. Some put the challenge in a base64 ``PAYMENT-REQUIRED`` header
(v2) with an empty ``{}`` body; others put the plain JSON directly in the
body (v1, and sometimes v2 too, as seen live from oklink). Some redirect
before the 402; some 405 on whichever verb we tried first; some offer one
payment scheme, some offer several. This module absorbs all of that so a
caller gets one normalized shape back, or a precise account of what could
not be recovered — it never raises on a merely-unusual shape, only on one
where nothing usable survives.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx

# Externally-defined wire names — keep byte-for-byte, they are not ours to rename.
PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
X_PAYMENT_HEADER = "X-PAYMENT"  # v1
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"  # v2
X_PAYMENT_RESPONSE_HEADER = "X-PAYMENT-RESPONSE"  # v1
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"  # v2

Version = Literal[1, 2]
Source = Literal["header", "body"]
Outcome = Literal["unsupported_network", "payable"]


class ChallengeParseError(Exception):
    """A 402 was found but no usable payment requirement could be recovered.

    `missing` names the precise field/shape that was absent, so the caller can
    report something more useful than "malformed 402".
    """

    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(f"402 challenge is missing required field: {missing}")


@dataclass
class NormalizedRequirement:
    scheme: str
    network: str  # raw network string, whatever shape the seller sent
    chain_id: int | None  # parsed from CAIP-2 "eip155:<id>"; None otherwise
    amount_units: str  # smallest-unit amount, whichever field name the version used
    asset: str | None
    pay_to: str | None
    max_timeout_seconds: int | None
    extra: dict[str, Any]

    # Duck-type alias so this drops into code written against the SDK's
    # PaymentRequirementsV1 (payer.pay(), price checks) unchanged.
    @property
    def max_amount_required(self) -> str:
        return self.amount_units


@dataclass
class NormalizedChallenge:
    version: Version
    source: Source  # where the payload was found: header vs body
    verb: str  # HTTP verb that produced the 402
    resource_url: str | None
    description: str | None
    selected: NormalizedRequirement  # preferred entry ("exact" if offered)
    alternatives: list[NormalizedRequirement]  # every other accepts[] entry — recorded, not dropped
    raw: dict[str, Any]  # the decoded challenge JSON, verbatim


@dataclass
class FetchResult:
    """One probe round, with redirects already followed and the verb settled."""
    response: httpx.Response
    verb: str


async def fetch_with_verb_fallback(
    http: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float = 10.0,
) -> FetchResult:
    """Probe `url`, following redirects, retrying the other verb once on 405.

    A 307/308 (or any redirect httpx follows) lands on the final hop before we
    ever look at the status code — PixelBrief, for one, redirects to a `www.`
    subdomain before quoting its 402.
    """
    method = method.upper()
    resp = await http.request(method, url, headers=headers, json=json_body,
                              timeout=timeout, follow_redirects=True)
    if resp.status_code == 405:
        other = "POST" if method == "GET" else "GET"
        resp = await http.request(other, url, headers=headers, json=json_body,
                                  timeout=timeout, follow_redirects=True)
        return FetchResult(resp, other)
    return FetchResult(resp, method)


def extract_challenge_payload(resp: httpx.Response) -> tuple[dict, Source] | None:
    """Header first, body fallback. Returns (decoded_json, source) or None."""
    header_val = resp.headers.get(PAYMENT_REQUIRED_HEADER)
    if header_val:
        try:
            data = json.loads(base64.b64decode(header_val, validate=False))
        except (binascii.Error, ValueError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            return data, "header"
        # header present but unusable — fall through and try the body anyway

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        return data, "body"
    return None


def _detect_version(payload: dict, entries: list[Any]) -> Version:
    v = payload.get("x402Version", payload.get("x402_version"))
    if v == 1:
        return 1
    if v == 2:
        return 2
    # No explicit/valid version field: infer from accepts[] entry shape.
    for e in entries:
        if not isinstance(e, dict):
            continue
        if "maxAmountRequired" in e or "max_amount_required" in e:
            return 1
        if "amount" in e:
            return 2
    return 2  # current protocol default, per the SDK's own convention


def _coerce_accepts_list(payload: dict) -> list[Any]:
    """Recover a flat list of candidate entries from whatever shape `accepts` is.

    Handles the documented cases plus real-world sloppiness: missing entirely,
    a single object instead of a list, or a list containing nested lists.
    """
    accepts = payload.get("accepts")
    if accepts is None:
        return []
    if isinstance(accepts, dict):
        return [accepts]
    if isinstance(accepts, list):
        flat: list[Any] = []
        for e in accepts:
            if isinstance(e, list):
                flat.extend(e)  # nested accepts[[...]] -> flatten one level
            else:
                flat.append(e)
        return flat
    return []


def _parse_chain_id(network: Any) -> int | None:
    if not isinstance(network, str) or not network.startswith("eip155:"):
        return None
    try:
        return int(network.split(":", 1)[1])
    except ValueError:
        return None


def _normalize_entry(entry: Any) -> NormalizedRequirement | None:
    if not isinstance(entry, dict):
        return None
    scheme = entry.get("scheme")
    network = entry.get("network")
    amount = entry.get("amount") or entry.get("maxAmountRequired") or entry.get("max_amount_required")
    if not scheme or not network or not amount:
        return None  # unusable entry — caller decides what to report as missing
    extra = entry.get("extra")
    return NormalizedRequirement(
        scheme=scheme,
        network=network,
        chain_id=_parse_chain_id(network),
        amount_units=str(amount),
        asset=entry.get("asset"),
        pay_to=entry.get("payTo") or entry.get("pay_to"),
        max_timeout_seconds=entry.get("maxTimeoutSeconds") or entry.get("max_timeout_seconds"),
        extra=extra if isinstance(extra, dict) else {},
    )


def _name_first_missing_field(entry: Any) -> str:
    if not isinstance(entry, dict):
        return "accepts[] entry (not a JSON object)"
    checks = (
        ("scheme", ("scheme",)),
        ("network", ("network",)),
        ("amount", ("amount", "maxAmountRequired", "max_amount_required")),
    )
    for name, aliases in checks:
        if not any(a in entry for a in aliases):
            return name
    return "accepts[] entry"


def parse_challenge_payload(payload: dict, *, source: Source, verb: str) -> NormalizedChallenge:
    """Normalize a decoded 402 JSON body into one version-agnostic shape.

    Raises ChallengeParseError naming the precise missing piece when nothing
    usable can be recovered — never a generic "malformed" message.
    """
    if not isinstance(payload, dict):
        raise ChallengeParseError("challenge body is not a JSON object")

    raw_entries = _coerce_accepts_list(payload)
    if not raw_entries:
        raise ChallengeParseError("accepts")

    version = _detect_version(payload, raw_entries)
    normalized = [n for e in raw_entries if (n := _normalize_entry(e)) is not None]
    if not normalized:
        raise ChallengeParseError(_name_first_missing_field(raw_entries[0]))

    selected = next((n for n in normalized if n.scheme == "exact"), normalized[0])
    alternatives = [n for n in normalized if n is not selected]

    resource = payload.get("resource")
    if isinstance(resource, dict):
        resource_url = resource.get("url")
        description = resource.get("description")
    else:
        resource_url = resource if isinstance(resource, str) else None
        description = payload.get("description")

    return NormalizedChallenge(
        version=version, source=source, verb=verb,
        resource_url=resource_url, description=description,
        selected=selected, alternatives=alternatives, raw=payload,
    )


def classify_payability(
    challenge: NormalizedChallenge,
    *,
    allowed_networks: tuple[str, ...] | set[str],
    payer_label: str = "our payer",
) -> tuple[Outcome, str]:
    """The second and third of the three reportable outcomes.

    (The first — unparseable — is signaled by ChallengeParseError instead,
    since there is no challenge to classify at that point.)
    """
    req = challenge.selected
    allowed = {n.lower() for n in allowed_networks}
    if (req.network or "").lower() in allowed:
        return "payable", f"well-formed 402 (x402 v{challenge.version}): {req.scheme} on {req.network}"
    asset_bit = f" with asset {req.asset}" if req.asset else ""
    return (
        "unsupported_network",
        f"well-formed 402 (x402 v{challenge.version}) but network {req.network!r}"
        f"{asset_bit} is not supported by {payer_label}",
    )


def payment_header_name(version: Version) -> str:
    """The request header the detected protocol version expects a signed payment in."""
    return X_PAYMENT_HEADER if version == 1 else PAYMENT_SIGNATURE_HEADER


def settlement_header_name(version: Version) -> str:
    """The response header the detected protocol version settles through."""
    return X_PAYMENT_RESPONSE_HEADER if version == 1 else PAYMENT_RESPONSE_HEADER
