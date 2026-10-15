"""Feed parsing into a small, UI-friendly structure.

Kept free of web framework code so it can be reused by other tools
(for example a future podcast transcript ingester running locally).
"""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import feedparser

SUMMARY_MAX = 300
TITLE_MAX = 500
_TAG_RE = re.compile(r"</?[a-zA-Z!][^>]*>")


class FeedParseError(ValueError):
    """Raised when the content is not a recognisable RSS or Atom feed."""


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "head"}
    _BREAK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def strip_html(value: str | None) -> str:
    """Remove all markup and return collapsed plain text."""
    if not value:
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(value)
        extractor.close()
        text = "".join(extractor.parts)
    except Exception:
        text = _TAG_RE.sub(" ", value)
    # Unescape again for double-encoded entities, then drop any tags that hid inside them.
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"


def _safe_link(link: str | None, base: str | None) -> str:
    if not link:
        return ""
    link = urljoin(base or "", link.strip())
    return link if urlsplit(link).scheme in ("http", "https") else ""


def _published(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                ts = calendar.timegm(value)
                return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _enclosures(entry, base: str | None) -> list[dict]:
    out = []
    for enc in entry.get("enclosures", []) or []:
        url = _safe_link(enc.get("href") or enc.get("url"), base)
        if not url:
            continue
        length = enc.get("length")
        try:
            length = int(length) if length not in (None, "") else None
        except (TypeError, ValueError):
            length = None
        out.append({"url": url, "type": enc.get("type") or "", "length": length})
    return out


def _entry_id(entry, link: str, title: str) -> str:
    raw = entry.get("id") or entry.get("guid") or link
    if not raw:
        raw = hashlib.sha256(f"{title}|{_published(entry)}".encode()).hexdigest()[:16]
    return str(raw)[:500]


def parse_feed(content: bytes | str, source_url: str | None = None) -> dict:
    """Parse RSS or Atom content.

    Returns {"title", "link", "items": [{id, title, link, summary, published, enclosures}]}.
    Enclosures (audio/video links) are preserved for future use; the UI ignores them.
    """
    parsed = feedparser.parse(content, sanitize_html=False, resolve_relative_uris=False)
    if not parsed.get("version") and not parsed.entries:
        raise FeedParseError("Not a valid RSS or Atom feed")

    feed = parsed.feed
    base = feed.get("link") or source_url
    items = []
    for entry in parsed.entries:
        title = truncate(strip_html(entry.get("title")), TITLE_MAX)
        link = _safe_link(entry.get("link"), base)
        raw_summary = entry.get("summary") or ""
        if not raw_summary and entry.get("content"):
            raw_summary = entry.content[0].get("value", "")
        summary = truncate(strip_html(raw_summary), SUMMARY_MAX)
        if not title:
            title = truncate(summary, 120) or "(untitled)"
        items.append(
            {
                "id": _entry_id(entry, link, title),
                "title": title,
                "link": link,
                "summary": summary,
                "published": _published(entry),
                "enclosures": _enclosures(entry, base),
            }
        )

    return {
        "title": truncate(strip_html(feed.get("title")), TITLE_MAX) or (source_url or "Untitled feed"),
        "link": _safe_link(feed.get("link"), source_url),
        "items": items,
    }
