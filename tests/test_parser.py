import pytest

from app.parser import FeedParseError, parse_feed, strip_html, truncate


def test_rss_parsing(rss_bytes):
    data = parse_feed(rss_bytes, "https://example.com/feed")
    assert data["title"] == "Example & News"
    assert len(data["items"]) == 3
    first = data["items"][0]
    assert first["id"] == "item-a"
    assert first["title"] == "Bushfire warning for Gippsland"
    assert first["link"] == "https://example.com/a"
    assert first["published"] == "2026-10-12T10:00:00Z"
    assert first["summary"] == "Residents should act now."
    assert first["enclosures"] == []


def test_rss_relative_link_and_enclosure_preserved(rss_bytes):
    item = parse_feed(rss_bytes, "https://example.com/feed")["items"][1]
    assert item["link"] == "https://example.com/episodes/12"
    assert item["enclosures"] == [
        {"url": "https://cdn.example.com/ep12.mp3", "type": "audio/mpeg", "length": 12345678}
    ]


def test_summary_capped_at_300_chars(rss_bytes):
    item = parse_feed(rss_bytes)["items"][1]
    assert 0 < len(item["summary"]) <= 300
    assert item["summary"].endswith("…")


def test_unsafe_links_dropped_and_missing_date(rss_bytes):
    item = parse_feed(rss_bytes)["items"][2]
    assert item["link"] == ""
    assert item["published"] is None
    assert item["id"]


def test_atom_parsing(atom_bytes):
    data = parse_feed(atom_bytes, "https://atom.example.org/feed")
    assert data["title"] == "Atom Example"
    first, second = data["items"]
    assert first["id"] == "urn:uuid:entry-1"
    assert first["title"] == "First entry"
    assert first["summary"] == "Hello & world"
    assert first["published"] == "2026-10-12T12:00:00Z"
    assert second["title"] == "Second entry"
    assert second["summary"] == "Content body"
    assert second["published"] == "2026-10-09T23:15:00Z"
    assert second["enclosures"][0]["url"] == "https://atom.example.org/2.m4a"


def test_strip_html_removes_everything():
    raw = '<p>Hi <b>there</b></p><script>evil()</script><style>x{}</style>&lt;i&gt;x&lt;/i&gt; &amp;amp; 5 &lt; 6'
    assert strip_html(raw) == "Hi there x & 5 < 6"
    assert "<" not in strip_html('<img src=x onerror="alert(1)">')
    assert strip_html(None) == ""


def test_no_html_in_any_output(rss_bytes, atom_bytes):
    for content in (rss_bytes, atom_bytes):
        for item in parse_feed(content)["items"]:
            assert "<" not in item["title"] and ">" not in item["title"]
            assert "<" not in item["summary"] and ">" not in item["summary"]


def test_truncate():
    assert truncate("short", 300) == "short"
    assert len(truncate("a " * 400, 300)) <= 300


def test_not_a_feed():
    with pytest.raises(FeedParseError):
        parse_feed(b"<!doctype html><html><body>hello</body></html>")
