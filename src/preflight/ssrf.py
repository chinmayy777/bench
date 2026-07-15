"""Target URL guard: public HTTPS only; block private/loopback/link-local/metadata ranges."""
from __future__ import annotations
import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
        "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
        "::1/128", "fc00::/7", "fe80::/10",
    )
]


class TargetRejected(ValueError):
    pass


def validate_target_url(url: str, allow_local: bool = False) -> str:
    """Return normalized URL or raise TargetRejected with a user-facing reason."""
    try:
        p = urlparse(url.strip())
    except Exception:
        raise TargetRejected("target_url is not a valid URL")
    if p.scheme not in ("https", "http"):
        raise TargetRejected("target_url must be http(s)")
    if p.scheme != "https" and not allow_local:
        raise TargetRejected("target_url must use https")
    if not p.hostname:
        raise TargetRejected("target_url has no host")
    if allow_local and p.hostname in ("127.0.0.1", "localhost", "::1"):
        return url
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise TargetRejected(f"cannot resolve host {p.hostname!r}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if any(ip in net for net in BLOCKED_NETS):
            raise TargetRejected(f"host resolves to blocked address {ip}")
    return url
