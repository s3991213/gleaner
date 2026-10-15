"""URL validation, SSRF protection and rate limiting for the public feed proxy."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import deque
from urllib.parse import urlsplit

import httpcore

ALLOWED_SCHEMES = {"http", "https"}

# Addresses that are publicly routable on paper but reach cloud internals.
EXTRA_BLOCKED_NETWORKS = [
    ipaddress.ip_network("168.63.129.16/32"),  # Azure WireServer / platform DNS
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
]


class BlockedURLError(ValueError):
    """Raised when a URL or the address it resolves to is not allowed."""


def is_ip_blocked(ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped or addr.sixtofour
        if mapped is not None:
            addr = mapped
    if any(addr in net for net in EXTRA_BLOCKED_NETWORKS):
        return True
    # is_global is False for private, loopback, link-local (incl. 169.254.169.254),
    # shared address space, reserved and unspecified ranges.
    return (
        not addr.is_global
        or addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def check_url_syntax(url: str) -> tuple[str, int]:
    """Validate scheme and shape of a URL. Returns (hostname, port)."""
    if not url or len(url) > 2048:
        raise BlockedURLError("URL is missing or too long")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BlockedURLError("Malformed URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError("Only http and https URLs are allowed")
    if not parts.hostname:
        raise BlockedURLError("URL has no host")
    if parts.username or parts.password:
        raise BlockedURLError("Credentials in URLs are not allowed")
    return parts.hostname, port or (443 if scheme == "https" else 80)


async def resolve_host(host: str, port: int) -> list[str]:
    """Resolve a hostname to the list of IP addresses it points at."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedURLError(f"Could not resolve host {host}") from exc
    return list(dict.fromkeys(info[4][0] for info in infos))


async def resolve_safe(host: str, port: int) -> list[str]:
    """Resolve a host and fail if any resulting address is not public."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    ips = [str(literal)] if literal else await resolve_host(host, port)
    if not ips:
        raise BlockedURLError(f"Could not resolve host {host}")
    for ip in ips:
        if is_ip_blocked(ip):
            raise BlockedURLError("Destination address is not allowed")
    return ips


async def validate_url(url: str) -> None:
    """Full pre-flight check: syntax plus resolved destination."""
    host, port = check_url_syntax(url)
    await resolve_safe(host, port)


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Network backend that re-checks the destination IP at connect time.

    This closes the DNS rebinding gap between validate_url() and the actual
    connection: the socket is opened to an address that was just verified.
    """

    def __init__(self) -> None:
        from httpcore._backends.auto import AutoBackend

        self._backend = AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            ips = await resolve_safe(host, port)
        except BlockedURLError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        return await self._backend.connect_tcp(
            ips[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("Unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class RateLimiter:
    """Sliding window limiter kept in memory, per client key."""

    def __init__(self, limit: int = 60, window: float = 60.0, clock=time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = clock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        cutoff = now - self.window
        if now - self._last_sweep > self.window:
            self._sweep(cutoff)
            self._last_sweep = now
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _sweep(self, cutoff: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()
