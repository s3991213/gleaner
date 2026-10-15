# Building a free, app-like RSS reader and shipping it to Azure Container Apps

> Draft. Sections marked **TODO** get filled in with real output as the deploy happens.
> Media placeholders look like **[VIDEO: ...]**, **[CAST: ...]** (asciinema terminal recording) and **[SCREENSHOT: ...]**.

## What I built

Gleaner is a small web app that pulls updates from sites I care about into one list. I pick RSS feeds, add keywords like "bushfire" or "RTX", and matching items get highlighted. I save it to my phone's home screen and it opens like an app.

The rules I set myself:

- **Free to run.** It has to stay inside Azure's free allowance.
- **No accounts, no database.** Everything a user chooses lives in their own browser.
- **One container.** A Python server sends the page to the browser and fetches feeds for it.

**[VIDEO: phone-sized demo: tutorial, picking starter feeds, keyword highlights]**

## How it fits together

- **Browser:** plain HTML, CSS and JavaScript, with no build step. Feeds, keywords and read items are kept in localStorage.
- **Server:** FastAPI with three endpoints: `/api/feed` fetches and cleans up a feed, `/api/starter-feeds` returns the curated list, and `/healthz` is the health check.
- **Hosting:** Azure Container Apps, set to scale to zero, so nothing runs (and nothing is billed) while nobody is using it.
- **Image registry:** GitHub Container Registry (ghcr.io), which is free for public images.
- **CI/CD:** GitHub Actions, which tests, builds and deploys on every push to `main`.

### Why a feed proxy needs care

Browsers can't fetch most feeds directly because of CORS, so the server fetches them instead. That makes it a public "fetch any URL" service, which is exactly what attackers look for: they could ask it to fetch addresses inside Azure, such as the cloud metadata service. Gleaner:

- only allows http and https
- looks up every hostname and refuses private, loopback, link-local and metadata addresses, before the request, after every redirect, and again at the moment it connects
- allows at most 3 redirects, waits at most 10 seconds and reads at most 2 MB
- limits each visitor to 60 requests a minute and caches each feed for 10 minutes
- strips all HTML from titles and summaries

## Running it locally

```bash
docker build --platform linux/amd64 -t gleaner:local .
docker run --rm -p 8000:8000 --platform linux/amd64 gleaner:local
```

`--platform linux/amd64` matters on Apple Silicon Macs: Azure runs Intel/AMD machines, so the image is built for that chip type, and the Mac runs it through emulation.

To try it on a phone, open `http://<your Mac's Wi-Fi address>:8000` with the phone on the same Wi-Fi. "Add to Home Screen" only works properly once the site has https, which Azure provides.

## Does it really cost nothing?

The "30 days free" part of an Azure free account is the $200 trial credit. Container Apps also has a monthly free allowance that carries on afterwards on pay-as-you-go: 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests per subscription per month. Because Gleaner scales to zero, a personal app sits well inside that.

To keep costs at zero I avoided Azure Container Registry, Key Vault, storage and databases, and created the Container Apps environment with `--logs-destination none` so there is no Log Analytics workspace to pay for. I also set a budget alert as a safety net.

**TODO:** budget alert screenshot. **[SCREENSHOT: Azure budget alert]**

## Step 1: Put the code on GitHub

**TODO:** commands and output.

**[CAST: creating the repo with gh]**

## Step 2: The first build on GitHub Actions

The workflow in `.github/workflows/ci-cd.yml` has three jobs:

1. **test:** ruff (code checks), pytest and a syntax check of the deploy script.
2. **build:** builds the amd64 image and pushes it to ghcr.io, tagged with the commit and `latest`.
3. **deploy:** logs in to Azure and points the container app at the new image, then checks `/healthz` until the app answers.

The deploy job is skipped until Azure is set up, so the first push only tests and builds.

**TODO:** making the ghcr package public. **[SCREENSHOT: GitHub Actions run] [SCREENSHOT: package visibility setting]**

## Step 3: The first deploy, by hand

**TODO:** `az login`, `deploy/deploy.sh`, output and the live URL.

**[CAST: running deploy.sh] [VIDEO: Azure portal walkthrough of the created resources]**

## Step 4: Let GitHub deploy without storing a password

Instead of saving an Azure password in GitHub, the workflow uses OpenID Connect. Azure is told to trust tokens from this one repository's `production` environment, and the GitHub identity can only change the Gleaner resource group.

**TODO:** `deploy/setup-github-oidc.sh` output, then a push that deploys on its own.

**[CAST: running setup-github-oidc.sh] [SCREENSHOT: full green pipeline]**

## Step 5: On the phone

**TODO:** Add to Home Screen on iOS and Android.

**[VIDEO: phone screen recording, opening from the home screen]**

## What I'd do next

**TODO**
