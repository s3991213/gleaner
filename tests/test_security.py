import asyncio

import pytest

from app.security import BlockedURLError, RateLimiter, check_url_syntax, is_ip_blocked, validate_url


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "168.63.129.16",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_blocked_ips(ip):
    assert is_ip_blocked(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip):
    assert not is_ip_blocked(ip)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/feed",
        "gopher://example.com",
        "javascript:alert(1)",
        "example.com/feed",
        "",
    ],
)
def test_non_http_schemes_rejected(url):
    with pytest.raises(BlockedURLError):
        check_url_syntax(url)


def test_credentials_rejected():
    with pytest.raises(BlockedURLError):
        check_url_syntax("https://user:pw@example.com/feed")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/feed",
        "http://10.0.0.1/feed",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/feed",
        "http://internal.test/feed",
        "http://metadata.test/feed",
        "http://mixed.test/feed",
        "http://v6local.test/feed",
    ],
)
def test_validate_url_blocks_private(url):
    with pytest.raises(BlockedURLError):
        asyncio.run(validate_url(url))


def test_validate_url_allows_public():
    asyncio.run(validate_url("https://example.com/feed"))


def test_rate_limiter_window():
    now = [0.0]
    limiter = RateLimiter(limit=3, window=60, clock=lambda: now[0])
    assert all(limiter.allow("a") for _ in range(3))
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 61
    assert limiter.allow("a")
