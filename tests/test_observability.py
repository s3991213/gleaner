import json
import logging

import pytest

from app.main import app
from app.observability import ClientHasher, JsonFormatter

FEED = "https://example.com/private/feed?token=s3cr3t-token"


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def request_logs():
    handler = ListHandler()
    logger = logging.getLogger("gleaner.request")
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


def formatted(records) -> str:
    fmt = JsonFormatter()
    return "\n".join(fmt.format(r) for r in records)


def test_healthz_reports_version(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_request_log_is_structured_and_private(client, rss_bytes, request_logs):
    import respx

    with respx.mock:
        respx.get(FEED).respond(200, content=rss_bytes)
        client.get("/api/feed", params={"url": FEED}, headers={"x-forwarded-for": "203.0.113.77"})

    assert len(request_logs) == 1
    line = json.loads(formatted(request_logs))
    assert line["msg"] == "request"
    assert line["path"] == "/api/feed"
    assert line["status"] == 200
    assert line["feed_host"] == "example.com"
    assert line["cache"] == "miss"
    assert len(line["client"]) == 16
    assert "version" in line and "duration_ms" in line

    text = formatted(request_logs)
    assert "203.0.113.77" not in text
    assert "s3cr3t-token" not in text
    assert "/private/feed" not in text


def test_cache_hit_and_errors_logged(client, rss_bytes, request_logs):
    import respx

    with respx.mock:
        respx.get(FEED).respond(200, content=rss_bytes)
        client.get("/api/feed", params={"url": FEED})
        client.get("/api/feed", params={"url": FEED})
    client.get("/api/feed", params={"url": "http://127.0.0.1/admin"})

    lines = [json.loads(JsonFormatter().format(r)) for r in request_logs]
    assert [entry["cache"] for entry in lines[:2]] == ["miss", "hit"]
    assert lines[2]["status"] == 400
    assert lines[2]["error"] == "Destination address is not allowed"


def test_healthz_not_logged(client, request_logs):
    client.get("/healthz")
    assert request_logs == []


def test_client_hasher_is_keyed_and_stable():
    a = ClientHasher("key-one")
    assert a("1.2.3.4") == a("1.2.3.4")
    assert a("1.2.3.4") != ClientHasher("key-two")("1.2.3.4")
    assert a.configured
    assert not ClientHasher("").configured


def test_app_has_hasher():
    assert app.state.client_hasher("1.2.3.4") != "1.2.3.4"
