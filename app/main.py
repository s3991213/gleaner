"""Gleaner: FastAPI app serving the static frontend and a small feed proxy API."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.fetcher import FeedFetcher, FetchError
from app.observability import APP_VERSION, ClientHasher, configure_logging
from app.security import BlockedURLError, RateLimiter

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
STARTER_FEEDS_FILE = ROOT / "starter_feeds.yaml"

configure_logging()
log = logging.getLogger("gleaner.request")


def load_starter_feeds(path: Path = STARTER_FEEDS_FILE) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {"categories": data.get("categories", []), "templates": data.get("templates", [])}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.getLogger("gleaner.app").info(
        "startup", extra={"log_hash_key_configured": app.state.client_hasher.configured}
    )
    yield
    await app.state.fetcher.aclose()


app = FastAPI(title="Gleaner", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.state.fetcher = FeedFetcher()
app.state.rate_limiter = RateLimiter(limit=60, window=60.0)
app.state.starter_feeds = load_starter_feeds()
app.state.client_hasher = ClientHasher()


def client_ip(request: Request) -> str:
    # Azure Container Apps ingress appends the real client address as the last
    # X-Forwarded-For entry, so the rightmost value is the one we can trust.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_and_log(request: Request, call_next):
    started = time.perf_counter()
    ip = client_ip(request)
    if request.url.path.startswith("/api/") and not request.app.state.rate_limiter.allow(ip):
        response = JSONResponse(
            {"error": "Too many requests, please wait a minute"},
            status_code=429,
            headers={"Retry-After": "60"},
        )
    else:
        response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    log_request(request, response.status_code, started, ip)
    return response


def log_request(request: Request, status: int, started: float, ip: str) -> None:
    if request.url.path == "/healthz":
        return  # probes and uptime checks would drown out real traffic
    # Never log the query string or full feed URL: private feeds often embed tokens.
    fields = {
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "client": request.app.state.client_hasher(ip),
    }
    for key in ("feed_host", "cache", "error"):
        value = getattr(request.state, key, None)
        if value is not None:
            fields[key] = value
    log.log(logging.WARNING if status >= 500 else logging.INFO, "request", extra=fields)


@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz():
    return {"status": "ok", "version": APP_VERSION}


def feed_host(url: str) -> str:
    try:
        return (urlsplit(url.strip()).hostname or "")[:255]
    except ValueError:
        return ""


@app.get("/api/feed")
async def api_feed(request: Request, url: str = Query(..., max_length=2048)):
    fetcher = request.app.state.fetcher
    request.state.feed_host = feed_host(url)
    request.state.cache = "hit" if fetcher.cache.get(url.strip()) is not None else "miss"
    try:
        data = await fetcher.get_feed(url)
    except BlockedURLError as exc:
        request.state.error = str(exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except FetchError as exc:
        request.state.error = str(exc)
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/starter-feeds")
async def api_starter_feeds(request: Request):
    return request.app.state.starter_feeds


# PWA files are served from the root so the service worker controls the whole app.
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json", include_in_schema=False)
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
