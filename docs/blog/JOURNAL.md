# Build journal

A dated record of what was done, what broke and how it was fixed. Source material for the blog post. Times are AEDT (Melbourne). Recordings are in `casts/` (play with `asciinema play casts/<file>.cast`, or upload to asciinema.org to embed).

## 2026-10-13: Build the app

- Wrote the spec as `CLAUDE.md`: a mobile-first RSS reader, FastAPI plus vanilla JS, all user data in localStorage, Azure Container Apps on the free tier, no accounts or database. It included a seven-point definition of done.
- Built the backend: `app/security.py` (URL checks, SSRF protection, rate limiter), `app/fetcher.py` (redirects, limits, cache), `app/parser.py` (RSS and Atom into plain-text items, audio enclosures kept for a future podcast feature) and `app/main.py`.
- **SSRF design:** every hop is checked before the request, and a custom httpx network backend checks the IP again at the moment of connecting. That closes the DNS rebinding gap between "check" and "connect". Azure's `168.63.129.16` is blocked as well as the usual private, loopback, link-local and metadata ranges.
- Built the frontend: the Feed, My Feeds and Discover screens, pull to refresh, unread toggle, keyword highlighting built without `innerHTML`, a 4-step tutorial, a service worker that caches only the app shell, and light and dark themes. Icons were drawn as SVG and rendered to PNG with headless Chromium.
- **Starter feed verification** (live):
  - The BoM warnings feed returned **403** to the User-Agent `Gleaner/1.0 (RSS reader; +https://github.com/)` but 200 to `Gleaner/1.0`. Switched to the short one.
  - **NASA APOD** `apod.rss` now redirects to an HTML page at science.nasa.gov. Dropped it and noted it in the README.
  - **Reddit** returned 429 for a few minutes (rate limiting by IP), then passed. It may be unreliable from cloud IPs.
- **Definition of done:** pytest (68 tests, no network), ruff, the live starter-feed check (9 of 9), the amd64 Docker build, the container smoke test, Playwright at 390x844 (tutorial shows, skips, stays hidden on reload, adding a feed shows items) and `bash -n` all passed.
- **Local environment snag:** Homebrew Python 3.14 couldn't create a venv (`ensurepip` failed), so I used miniconda's Python 3.13.

## 2026-10-15: Run, publish, deploy

- Ran the container locally. Port 8000 was taken by another project's container (`gnosis`), so I either used `-p 8001:8000` or stopped that container. The `platform (linux/amd64) does not match` warning is expected on Apple Silicon; `--platform linux/amd64` silences it.
- Tested on a phone over Wi-Fi at the Mac's LAN address (port 8000). Full PWA install needs https, which came with Azure.
- **Git:** my first commit got pushed, and I then recommitted locally, which produced a merge. I cleaned it up with `git reset --hard <my commit>` and `git push --force-with-lease`. History is now `Initial commit`, then the app commit.
- **First pipeline run:** test and build passed, the image was published to `ghcr.io/s3991213/gleaner`, and deploy was skipped (no Azure variables yet).
- **The image was private.** An anonymous pull of the manifest returned **403**. I made the package public in its settings, and the pull then returned 200.
- `az` and `gh` were installed with Homebrew. Logging in on the websites isn't enough: each CLI needs its own `az login` / `gh auth login --scopes workflow`.
- **`az extension add containerapp` failed:** `pyexpat ... Symbol not found: _XML_SetAllocTrackerActivationThreshold`. Homebrew's Python 3.14 needs a newer libexpat than macOS 26.2 ships. Not needed: `az containerapp` is built into az 2.91. I removed the extension step everywhere.
- **First deploy** (`casts/03-first-deploy.cast`): registering `Microsoft.App` took about 2.5 minutes, then the resource group, the environment (`--logs-destination none`) and the app were created. Live at `https://gleaner.livelywater-a1a76a35.australiaeast.azurecontainerapps.io`. Checks: `/healthz` 200 in 0.18 s, Hacker News returned 20 items, `169.254.169.254` and `168.63.129.16` both returned 400, and the Playwright suite passed against the live URL.
- Noticed `HEAD /` returned 405, which matters for some uptime monitors. Fixed by allowing HEAD on `/` and `/healthz`.
- **Budget:** `gleaner-5-dollar-budget`, $5 a month on the subscription, with emails at 20% actual, 100% actual and 100% forecast. Created via `az rest` because `az consumption budget create` can't set up email alerts.
- **GitHub to Azure without a password** (`casts/04-github-oidc.cast`): an app registration with Contributor on `gleaner-rg` only, a federated credential and repo variables.
- **First automatic deploy failed:** `AADSTS700213: No matching federated identity record found for presented assertion subject 'repo:s3991213@176739961/gleaner@1420144093:environment:production'`. GitHub now puts the owner and repo **numeric IDs** in the token subject for newer repos. I added a credential for that form, the script now creates both, and re-running only the failed job passed.
- Pushed again with no manual steps: test, build and deploy all passed, and production moved to `sha-a48cdea`.

## 2026-10-15: Week 2, secure delivery

- **Logging** (`app/observability.py`): JSON lines on stdout. Each request logs the method, path, status, duration, a keyed HMAC of the client IP, and for feeds the host, cache hit or miss and any error. Raw IPs, query strings and full feed URLs are never logged, because private feed URLs often contain tokens. Tests prove an IP and a `?token=` value don't appear. `/healthz` isn't logged. uvicorn's access log is turned off so it can't leak IPs.
- **Version reporting:** the image is built with `APP_VERSION=sha-xxxxxxx`, and `/healthz` returns it, so a deploy (or rollback) can prove which version is serving.
- **Staging app** (`casts/05-staging-app.cast`): `gleaner-staging`, in the same environment, also scaling to zero.
- **Key Vault:** the first run (`casts/06-keyvault-first-attempt-failed.cast`) failed with `MissingSubscriptionRegistration` for `Microsoft.KeyVault`, which a new subscription has to register once. The script now registers the provider first. The second run (`casts/06-keyvault.cast`) created:
  - the vault `gleaner-kv-0b9d4c` in its own resource group, `gleaner-secrets-rg`, so the GitHub deploy identity (Contributor on `gleaner-rg` only) can't manage it
  - one random secret per app, generated with `openssl rand`, written via a `chmod 600` temp file and never printed
  - a system-assigned identity on each app, with **Key Vault Secrets User scoped to that one secret**
  - a Key Vault reference as the app secret, exposed as `LOG_HASH_KEY`. The app config shows only the vault URL, not the value.
- **Log Analytics:** the first run (`casts/07-logging-first-attempt-timeout.cast`) created the workspace, but `az containerapp env update` returned `(ServerTimeout)` and the environment didn't change. The script is idempotent, and the re-run (`casts/07-logging.cast`) switched the environment to `log-analytics`. The workspace `gleaner-logs` has a 0.1 GB/day cap and 30-day retention.
- **GitHub environments** (`casts/08-github-environments.cast`): `staging` (no approval) and `production` (approval from s3991213), both limited to the `main` branch. Each has its own `AZURE_CONTAINERAPP_NAME` variable, and the shared Azure IDs stay as repository variables. Federated credentials were added for both environments, in both subject forms.
- **Pipeline** (`.github/workflows/`):
  - `ci-cd.yml`: lint, tests and a gitleaks secret scan of the full history (the test gate), then build one image, deploy it to staging, wait for approval, deploy it to production and publish release notes.
  - `deploy.yml`: a reusable deploy. It records the current image, rolls out a revision named after the version, and waits until that revision is the latest ready one **and** `/healthz` reports the version. Otherwise it rolls back to the recorded image automatically. It writes a deployment log table to the run summary.
  - `rollback.yml`: a "Run workflow" button for any environment and any already-built version. Production still needs approval.
  - Ran actionlint over all three, and the local gitleaks scans of history and working tree were clean.
- **Commit hygiene:** removed the AI co-author and session trailers from every commit and set the author to `Nipuna T <176739961+s3991213@users.noreply.github.com>` (the private GitHub email, so the commits link to my account without exposing a real address). Fixed globally in `~/.gitconfig`, which had `user.name = ravindu`. Rewrote history with `git filter-branch`, keeping dates and checking the final tree was byte-identical before force-pushing.
