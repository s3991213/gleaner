from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import security
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"

# Fake DNS so no test touches the real network.
FAKE_DNS = {
    "example.com": ["93.184.216.34"],
    "feeds.example.com": ["93.184.216.35"],
    "atom.example.org": ["93.184.216.36"],
    "internal.test": ["10.0.0.5"],
    "metadata.test": ["169.254.169.254"],
    "mixed.test": ["93.184.216.34", "127.0.0.1"],
    "v6local.test": ["::1"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def resolve(host: str, port: int) -> list[str]:
        if host in FAKE_DNS:
            return FAKE_DNS[host]
        raise security.BlockedURLError(f"Could not resolve host {host}")

    monkeypatch.setattr(security, "resolve_host", resolve)


@pytest.fixture(autouse=True)
def reset_state():
    app.state.fetcher.cache.clear()
    app.state.rate_limiter.reset()
    yield
    app.state.fetcher.cache.clear()
    app.state.rate_limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def rss_bytes() -> bytes:
    return (FIXTURES / "rss.xml").read_bytes()


@pytest.fixture
def atom_bytes() -> bytes:
    return (FIXTURES / "atom.xml").read_bytes()
