import pytest
from preflight.ssrf import TargetRejected, validate_target_url


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/mcp/", "https://localhost/mcp/",
    "https://169.254.169.254/latest/meta-data", "https://10.1.2.3/",
    "https://192.168.1.1/", "ftp://example.com/", "https:///nohost",
])
def test_blocked(url):
    with pytest.raises(TargetRejected):
        validate_target_url(url, allow_local=False)


def test_http_requires_local_mode():
    with pytest.raises(TargetRejected):
        validate_target_url("http://example.com/mcp/", allow_local=False)


def test_local_mode_allows_loopback():
    assert validate_target_url("http://127.0.0.1:8901/mcp/", allow_local=True)
