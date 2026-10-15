import asyncio

import httpx
import pytest
import respx

from app import fetcher as fetcher_mod
from app.fetcher import MAX_BYTES, FeedCache
from app.main import app

FEED = "https://example.com/feed"


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_starter_feeds(client):
    data = client.get("/api/starter-feeds").json()
    names = [c["name"] for c in data["categories"]]
    assert names == ["Australia", "Deals", "Tech", "Science"]
    assert any(f.get("popular") for c in data["categories"] for f in c["feeds"])
    assert len(data["templates"]) == 3


def test_index_and_pwa_files(client):
    assert "Gleaner" in client.get("/").text
    assert client.get("/manifest.json").json()["name"] == "Gleaner"
    assert client.get("/sw.js").status_code == 200


def test_feed_rss(client, mock, rss_bytes):
    mock.get(FEED).respond(200, content=rss_bytes, headers={"content-type": "application/rss+xml"})
    resp = client.get("/api/feed", params={"url": FEED})
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Example & News"
    assert {"id", "title", "link", "summary", "published"} <= set(data["items"][0])
    assert "<" not in data["items"][0]["summary"]


def test_feed_atom(client, mock, atom_bytes):
    url = "https://atom.example.org/feed.atom"
    mock.get(url).respond(200, content=atom_bytes)
    data = client.get("/api/feed", params={"url": url}).json()
    assert [i["title"] for i in data["items"]] == ["First entry", "Second entry"]


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/feed", "http://10.0.0.1/rss", "http://169.254.169.254/latest/meta-data/", "http://internal.test/x"],
)
def test_feed_ssrf_blocked(client, mock, url):
    route = mock.get(url).respond(200, content=b"<rss/>")
    resp = client.get("/api/feed", params={"url": url})
    assert resp.status_code == 400
    assert not route.called


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/feed", "gopher://example.com/"])
def test_feed_non_http_rejected(client, url):
    assert client.get("/api/feed", params={"url": url}).status_code == 400


def test_redirect_to_private_ip_blocked(client, mock):
    mock.get(FEED).respond(302, headers={"location": "http://internal.test/secret"})
    internal = mock.get("http://internal.test/secret").respond(200, content=b"secret")
    resp = client.get("/api/feed", params={"url": FEED})
    assert resp.status_code == 400
    assert not internal.called


def test_redirect_to_metadata_ip_blocked(client, mock):
    mock.get(FEED).respond(301, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    meta = mock.get("http://169.254.169.254/latest/meta-data/").respond(200)
    assert client.get("/api/feed", params={"url": FEED}).status_code == 400
    assert not meta.called


def test_redirect_to_public_followed(client, mock, rss_bytes):
    mock.get(FEED).respond(301, headers={"location": "https://feeds.example.com/rss"})
    mock.get("https://feeds.example.com/rss").respond(200, content=rss_bytes)
    assert client.get("/api/feed", params={"url": FEED}).status_code == 200


def test_too_many_redirects(client, mock):
    for i in range(5):
        mock.get(f"https://example.com/r{i}").respond(302, headers={"location": f"/r{i + 1}"})
    resp = client.get("/api/feed", params={"url": "https://example.com/r0"})
    assert resp.status_code == 502
    assert "redirects" in resp.json()["error"]


def test_size_cap_declared(client, mock):
    mock.get(FEED).respond(200, content=b"x" * (MAX_BYTES + 1))
    assert client.get("/api/feed", params={"url": FEED}).status_code == 413


def test_size_cap_streamed_without_length(client, mock):
    async def body():
        chunk = b"x" * 65536
        for _ in range(MAX_BYTES // len(chunk) + 2):
            yield chunk

    mock.get(FEED).mock(return_value=httpx.Response(200, content=body()))
    assert client.get("/api/feed", params={"url": FEED}).status_code == 413


def test_timeout(client, mock):
    mock.get(FEED).mock(side_effect=httpx.ReadTimeout("slow"))
    assert client.get("/api/feed", params={"url": FEED}).status_code == 504


def test_overall_deadline(client, mock, monkeypatch, rss_bytes):
    monkeypatch.setattr(fetcher_mod, "TIMEOUT_SECONDS", 0.2)

    async def slow(request):
        await asyncio.sleep(2)
        return httpx.Response(200, content=rss_bytes)

    mock.get(FEED).mock(side_effect=slow)
    assert client.get("/api/feed", params={"url": FEED}).status_code == 504


def test_upstream_error_status(client, mock):
    mock.get(FEED).respond(404)
    assert client.get("/api/feed", params={"url": FEED}).status_code == 502


def test_not_a_feed(client, mock):
    mock.get(FEED).respond(200, content=b"<html><body>nope</body></html>")
    assert client.get("/api/feed", params={"url": FEED}).status_code == 502


def test_cache_hit(client, mock, rss_bytes):
    route = mock.get(FEED).respond(200, content=rss_bytes)
    first = client.get("/api/feed", params={"url": FEED}).json()
    second = client.get("/api/feed", params={"url": FEED}).json()
    assert first == second
    assert route.call_count == 1


def test_cache_expires_after_ttl():
    now = [0.0]
    cache = FeedCache(ttl=600, clock=lambda: now[0])
    cache.set("k", {"v": 1})
    now[0] = 599
    assert cache.get("k") == {"v": 1}
    now[0] = 601
    assert cache.get("k") is None


def test_rate_limit(client):
    for _ in range(60):
        assert client.get("/api/starter-feeds").status_code == 200
    resp = client.get("/api/starter-feeds")
    assert resp.status_code == 429
    # A different client address has its own budget.
    other = client.get("/api/starter-feeds", headers={"x-forwarded-for": "203.0.113.9"})
    assert other.status_code == 200
    # Health checks are never limited.
    assert client.get("/healthz").status_code == 200


def test_rate_limit_uses_rightmost_forwarded_for(client):
    headers = {"x-forwarded-for": "1.2.3.4, 198.51.100.7"}
    for _ in range(60):
        client.get("/api/starter-feeds", headers=headers)
    spoofed = {"x-forwarded-for": "9.9.9.9, 198.51.100.7"}
    assert client.get("/api/starter-feeds", headers=spoofed).status_code == 429
    assert app.state.rate_limiter.allow("1.2.3.4")
