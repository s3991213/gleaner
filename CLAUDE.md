# Build: Gleaner

## Goal
A mobile-first web app where anyone can pick RSS feeds, set keyword filters, and read matching items in one clean list. Users save it to their phone home screen like an app. Hosted on Azure Container Apps, must stay within the free tier. No accounts, no database.

## Architecture
- One container: FastAPI serves the static frontend and a small API
- All user data (chosen feeds, keywords, read items, tutorial-seen flag) lives in the browser's localStorage. Wrap every localStorage call in try/catch; app must still work if storage is unavailable
- Server is stateless. ACA app with HTTP ingress, min replicas 0 (scale to zero)

## API
- GET /api/feed?url=<feed url>: server fetches and parses the feed (avoids browser CORS), returns JSON: title, items[{id, title, link, summary (plain text, max 300 chars), published}]
- GET /api/starter-feeds: returns the curated list from starter_feeds.yaml
- GET /healthz
- Security (required, it is a public proxy):
  - Only http/https URLs
  - Resolve hostname and block private, loopback, link-local and metadata IPs (SSRF protection), including after redirects
  - Max 3 redirects, 10s timeout, 2 MB response cap
  - Rate limit 60 requests/min per IP (in-memory is fine)
  - Cache each feed in memory for 10 minutes
  - Strip all HTML from titles/summaries before returning

## Frontend (vanilla HTML/CSS/JS, no build step)
- Screens: Feed list (merged, newest first, keyword matches highlighted), My Feeds (add by URL, toggle, delete, per-feed keywords), Discover (starter feeds grouped by category, one-tap add)
- Pull-to-refresh and a refresh button. Mark items read on tap; "Show unread only" toggle
- PWA: manifest.json, app icon (simple generated SVG/PNG), theme color, service worker caching the app shell only, so it opens like an app from the home screen
- Light and dark mode via prefers-color-scheme
- Works at 360px width, large tap targets

## First-time tutorial
Show on first visit only (localStorage flag), skippable, reopenable from a "?" button. 4 short steps as a card overlay:
1. Welcome: "Gleaner collects updates from sites you care about into one list."
2. Pick a starter pack: inline chips from Discover, preselect 3 popular ones
3. Keywords: "Add words like 'bushfire' or 'RTX' and matching items get highlighted." Show an example
4. Add to home screen: detect platform. iOS Safari: Share, then "Add to Home Screen". Android Chrome: menu, then "Install app" or "Add to Home screen". Desktop: bookmark tip

## starter_feeds.yaml (verify every URL returns a valid feed during build; drop any that fail and note it in README)
- Australia: ABC News Top Stories https://www.abc.net.au/news/feed/51120/rss.xml ; BoM Victoria warnings http://www.bom.gov.au/fwo/IDZ00059.warnings_vic.xml ; r/melbourne https://www.reddit.com/r/melbourne/.rss
- Deals: OzBargain https://www.ozbargain.com.au/deals/feed
- Tech: Hacker News front page https://hnrss.org/frontpage ; Product Hunt https://www.producthunt.com/feed ; Simon Willison on AI https://simonwillison.net/atom/everything/
- Science: arXiv AI papers https://rss.arxiv.org/rss/cs.AI ; NASA Astronomy Picture of the Day https://apod.nasa.gov/apod.rss ; USGS M4.5+ earthquakes https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom
- Templates shown in Discover as help text: GitHub releases https://github.com/OWNER/REPO/releases.atom ; YouTube channel https://www.youtube.com/feeds/videos.xml?channel_id=ID ; any subreddit https://www.reddit.com/r/NAME/.rss

## Structure
- app/main.py, app/fetcher.py, app/security.py, app/parser.py
- static/index.html, static/app.js, static/styles.css, static/manifest.json, static/sw.js, static/icons/
- starter_feeds.yaml
- tests/ pytest; mock HTTP with respx and fixture XML (RSS and Atom). No real network in unit tests
- requirements.txt: fastapi, uvicorn, httpx, feedparser, pyyaml, pytest, respx
- Dockerfile: python:3.12-slim, multi-stage, non-root user, uvicorn on port 8000
- .dockerignore must exclude CLAUDE.md, tests/, .git, __pycache__, docs/; plus .gitignore
- docs/ROADMAP.md: copy the "Future development" section below into it
- deploy/deploy.sh: az CLI creating resource group, ACA environment (logs-destination none), container app with external ingress port 8000, min replicas 0, max 2, image from ghcr.io. Do NOT run it
- README.md: what it is, local run, deploy steps, credit list of feed sources

## Constraints
- macOS Apple Silicon: build with --platform linux/amd64
- No Azure Container Registry, Key Vault, storage or database (cost). Use ghcr.io
- No em dashes anywhere in UI text, docs or comments
- No secrets needed; never hardcode any

## Definition of done (verify each, fix and repeat until all pass)
1. pytest all green, covering: RSS and Atom parsing, HTML stripping, SSRF blocks (127.0.0.1, 10.x, 169.254.169.254, redirect to private IP), non-http scheme rejected, size cap, timeout, rate limit, cache hit
2. ruff check passes
3. Script tests/check_starter_feeds.py fetches every starter feed live; all remaining entries in starter_feeds.yaml pass
4. docker build --platform linux/amd64 succeeds
5. docker run, then curl /healthz returns 200 and /api/feed?url=https://hnrss.org/frontpage returns items
6. Use a headless browser (playwright) at 390x844 to confirm: tutorial shows on first load, can be skipped, does not show on reload, adding a starter feed shows items
7. bash -n deploy/deploy.sh passes
Stop after all 7 pass. Do not push images or deploy.

## Future development (DO NOT BUILD NOW; only keep the code easy to extend)
Podcast transcript search: find the episode and timestamp where a phrase or concept was said.
- Ingest runs on the Mac, not Azure: read podcast RSS audio enclosures, use podcast:transcript tags when present, otherwise transcribe locally with mlx-whisper
- Chunk transcripts into ~30s segments with timestamps; SQLite FTS5 for exact phrases plus small embeddings for concept search
- Search API returns episode, timestamp, short snippet and a deep link. Never publish full transcripts
- To prepare now: keep feed parsing in app/parser.py reusable and preserve enclosure URLs (audio links) in parsed items, but do not show them in the UI yet
