"""Safe outbound fetching of feeds with redirects, limits and a small cache."""

from __future__ import annotations

import asyncio
import ssl
import time
from urllib.parse import urljoin

import certifi
import httpcore
import httpx

from app import security
from app.parser import parse_feed

MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 10.0
MAX_BYTES = 2 * 1024 * 1024
CACHE_TTL = 600.0
CACHE_MAX_ENTRIES = 500
USER_AGENT = "Gleaner/1.0"
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"


class FetchError(Exception):
    """Upstream problem. status_code is what the API should return."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class GuardedTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose connections only go to verified public addresses."""

    def __init__(self) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(cafile=certifi.where()),
            max_connections=20,
            network_backend=security.GuardedNetworkBackend(),
        )


def build_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport or GuardedTransport(),
        follow_redirects=False,
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
        headers={"User-Agent": USER_AGENT, "Accept": ACCEPT},
        trust_env=False,
    )


class FeedCache:
    def __init__(self, ttl: float = CACHE_TTL, max_entries: int = CACHE_MAX_ENTRIES, clock=time.monotonic):
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self._data: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> dict | None:
        hit = self._data.get(key)
        if not hit:
            return None
        if hit[0] < self.clock():
            self._data.pop(key, None)
            return None
        return hit[1]

    def set(self, key: str, value: dict) -> None:
        now = self.clock()
        if len(self._data) >= self.max_entries:
            for k in [k for k, (exp, _) in self._data.items() if exp < now]:
                del self._data[k]
            while len(self._data) >= self.max_entries:
                self._data.pop(next(iter(self._data)))
        self._data[key] = (now + self.ttl, value)

    def clear(self) -> None:
        self._data.clear()


async def fetch_bytes(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    """Fetch a URL, validating every hop. Returns (body, final_url)."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        await security.validate_url(current)
        async with client.stream("GET", current) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise FetchError("Redirect without a location")
                current = urljoin(current, location)
                continue
            if resp.status_code >= 400:
                raise FetchError(f"Feed server answered {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                raise FetchError("Feed is larger than 2 MB", 413)
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise FetchError("Feed is larger than 2 MB", 413)
            return bytes(body), current
    raise FetchError("Too many redirects")


class FeedFetcher:
    def __init__(self, client: httpx.AsyncClient | None = None, cache: FeedCache | None = None) -> None:
        self.client = client or build_client()
        self.cache = cache or FeedCache()

    async def get_feed(self, url: str) -> dict:
        url = url.strip()
        security.check_url_syntax(url)
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                body, final_url = await fetch_bytes(self.client, url)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise FetchError("Feed took too long to respond", 504) from exc
        except httpx.HTTPError as exc:
            raise FetchError("Could not reach the feed") from exc
        try:
            data = await asyncio.to_thread(parse_feed, body, final_url)
        except ValueError as exc:
            raise FetchError(str(exc) or "Could not parse feed") from exc
        self.cache.set(url, data)
        return data

    async def aclose(self) -> None:
        await self.client.aclose()
