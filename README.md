# Gleaner

Gleaner collects updates from sites you care about into one list. Pick RSS or Atom feeds, add keywords per feed, and read everything merged, newest first, with matches highlighted. Save it to your phone's home screen and it opens like an app.

- No accounts and no database. Your feeds, keywords, read items and settings live in your browser's localStorage.
- The server is a small stateless FastAPI app. It fetches feeds for the browser (avoiding CORS), parses them and returns plain-text JSON.
- Built to run on Azure Container Apps within the free tier: one container, scale to zero.

## Features

- **Feed**: merged list from all enabled feeds, newest first. Keyword matches are highlighted. Tap an item to open it and mark it read. "Show unread only" toggle, pull to refresh and a refresh button.
- **My Feeds**: add any feed by URL, switch feeds on or off, set keywords per feed, delete feeds.
- **Discover**: curated starter feeds grouped by category with one-tap add, plus URL patterns for GitHub releases, YouTube channels and subreddits.
- **First-time tutorial**: four short steps, skippable, reopenable from the "?" button.
- **PWA**: manifest, icons and a service worker that caches the app shell (never feed data). Light and dark mode follow your system setting.

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/feed?url=<feed url>` | Fetch and parse a feed. Returns `{title, link, items: [{id, title, link, summary, published, enclosures}]}`. Summaries are plain text, max 300 characters. |
| `GET /api/starter-feeds` | The curated list from `starter_feeds.yaml`. |
| `GET /healthz` | Liveness check. |

`/api/feed` is a public proxy, so it is locked down:

- Only `http` and `https` URLs; no credentials in URLs.
- The hostname is resolved and private, loopback, link-local, shared, reserved and cloud metadata addresses are blocked (including `169.254.169.254` and Azure's `168.63.129.16`). This is checked before every request, after every redirect, and again at socket connect time, which also defeats DNS rebinding.
- At most 3 redirects, a 10 second timeout and a 2 MB response cap.
- 60 requests per minute per client IP on `/api/*` (in memory).
- Each feed is cached in memory for 10 minutes.
- All HTML is stripped from titles and summaries, and item links that are not http or https are dropped.

## Run locally

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000.

### Tests and checks

```bash
.venv/bin/pytest                                  # unit tests, no network (respx + fixture XML)
.venv/bin/pip install ruff && .venv/bin/ruff check .
.venv/bin/python tests/check_starter_feeds.py      # live check of every starter feed
```

Browser check (needs a running server):

```bash
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
.venv/bin/python tests/e2e_browser.py http://127.0.0.1:8000
```

The PNG icons are rendered from the SVGs in `static/icons/` with `python tools/make_icons.py` (needs playwright).

### Docker

On Apple Silicon, build for the platform Azure runs:

```bash
docker build --platform linux/amd64 -t gleaner:local .
docker run --rm -p 8000:8000 gleaner:local
curl http://localhost:8000/healthz
```

## Deploy to Azure Container Apps

The image is hosted on GitHub Container Registry (ghcr.io), so no Azure Container Registry is needed. No Key Vault, storage or database is used, and no secrets are required.

1. Build and push the image, then make the package public in GitHub (Packages, Package settings, Change visibility) so Azure can pull it without credentials:

   ```bash
   echo "$GITHUB_TOKEN" | docker login ghcr.io -u OWNER --password-stdin
   docker build --platform linux/amd64 -t ghcr.io/OWNER/gleaner:latest .
   docker push ghcr.io/OWNER/gleaner:latest
   ```

2. Log in to Azure (Container Apps commands are built into recent versions of the az CLI):

   ```bash
   az login
   ```

3. Run the deploy script:

   ```bash
   IMAGE=ghcr.io/OWNER/gleaner:latest ./deploy/deploy.sh
   ```

   It creates a resource group, a Container Apps environment with `--logs-destination none` (no Log Analytics cost) and a container app with external ingress on port 8000, min replicas 0 and max replicas 2. Override `RESOURCE_GROUP`, `LOCATION`, `ENVIRONMENT` or `APP_NAME` with environment variables. Re-running it updates the app to the given image.

With scale to zero, the first request after a quiet period takes a few seconds while a replica starts.

## Feed sources

Starter feeds in Discover, with thanks to their publishers:

- **Australia**: [ABC News Top Stories](https://www.abc.net.au/news/feed/51120/rss.xml), [Bureau of Meteorology Victoria warnings](http://www.bom.gov.au/fwo/IDZ00059.warnings_vic.xml), [r/melbourne](https://www.reddit.com/r/melbourne/.rss)
- **Deals**: [OzBargain](https://www.ozbargain.com.au/deals/feed)
- **Tech**: [Hacker News front page via hnrss.org](https://hnrss.org/frontpage), [Product Hunt](https://www.producthunt.com/feed), [Simon Willison's Weblog](https://simonwillison.net/atom/everything/)
- **Science**: [arXiv cs.AI](https://rss.arxiv.org/rss/cs.AI), [USGS M4.5+ earthquakes, past day](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom)

All content belongs to its publishers. Gleaner shows titles, short plain-text summaries and links back to the source.

### Dropped during build verification

- **NASA Astronomy Picture of the Day** (`https://apod.nasa.gov/apod.rss`): the URL now redirects to an HTML page at science.nasa.gov, not a feed.

### Notes

- Reddit rate limits aggressively and sometimes returns HTTP 429, especially to cloud IP ranges. If r/melbourne shows as "Could not load", try again later.
- The Bureau of Meteorology rejects some User-Agent strings; Gleaner sends `Gleaner/1.0`, which works.

## Project layout

```
app/        main.py (routes), fetcher.py (safe fetching, cache), security.py (SSRF, rate limit), parser.py (RSS/Atom)
static/     index.html, app.js, styles.css, manifest.json, sw.js, icons/
tests/      pytest suite with fixtures, plus check_starter_feeds.py and e2e_browser.py
deploy/     deploy.sh for Azure Container Apps
docs/       ROADMAP.md
```

See [docs/ROADMAP.md](docs/ROADMAP.md) for planned podcast transcript search.
