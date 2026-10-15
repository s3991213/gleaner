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

To keep costs at zero I avoided Azure Container Registry, storage and databases. I started with the Container Apps environment on `--logs-destination none`, so there was no Log Analytics workspace to pay for. In week 2 I added Key Vault and Log Analytics, but in their near-free forms: Key Vault standard has no monthly fee (about $0.03 per 10,000 reads), and Log Analytics has a hard 0.1 GB/day cap, which stays inside its free 5 GB a month. I also set a budget alert as a safety net.

I created the budget with `az rest`, because `az consumption budget create` can't set up email alerts. It's on the whole subscription, $5 a month, and emails me at 20% actual spend, 100% actual and 100% forecast:

```bash
az rest --method put \
  --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.Consumption/budgets/gleaner-5-dollar-budget?api-version=2023-05-01" \
  --body @budget.json
```

**[SCREENSHOT: Azure budget alert in Cost Management]**

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

Log in, then run the deploy script with the image the pipeline built. I use the commit tag (`sha-4dc3f78`) rather than `latest`, so I know exactly which build is live:

```bash
az login
IMAGE=ghcr.io/s3991213/gleaner:sha-4dc3f78 ./deploy/deploy.sh
```

The script does four things:

1. **Registers the providers.** It enables Container Apps (`Microsoft.App`) on the subscription. On a brand new subscription this was the slowest part, about 2.5 minutes.
2. **Creates the resource group.** `gleaner-rg` in Australia East, the closest region to Melbourne.
3. **Creates the Container Apps environment.** `gleaner-env`, with `--logs-destination none`, which took a few minutes.
4. **Creates the container app.** Public https ingress to port 8000, 0.25 vCPU, 0.5 GiB, min replicas 0, max replicas 2.

At the end it prints the address:

```
Deployed: https://gleaner.livelywater-a1a76a35.australiaeast.azurecontainerapps.io
```

**[CAST: casts/03-first-deploy.cast]** **[VIDEO: Azure portal walkthrough of the created resources]**

### Checking it

```bash
U=https://gleaner.livelywater-a1a76a35.australiaeast.azurecontainerapps.io
curl $U/healthz                                                  # {"status":"ok"}
curl "$U/api/feed?url=https://hnrss.org/frontpage"               # 20 items
curl "$U/api/feed?url=http://169.254.169.254/metadata/instance"  # 400: Destination address is not allowed
```

The last check is the important one. `169.254.169.254` is the cloud metadata address, and the proxy refuses it on the real Azure network too. I also ran the Playwright browser check against the live URL: tutorial, skip, reload and adding a feed all passed.

### Two snags on the way

- **The image was private.** GitHub makes new container images private, and Azure can't pull a private image without credentials. The fix is a one-time switch in the package settings (Change visibility, Public).
- **`az extension add containerapp` failed on my Mac.** Homebrew's Python 3.14 was built against a newer XML library than macOS 26.2 ships, which broke the CLI's extension installer. The extension isn't needed anyway: `az containerapp` is built into recent versions of the Azure CLI.

## Step 4: Let GitHub deploy without storing a password

Instead of saving an Azure password in GitHub, the workflow uses OpenID Connect. Azure is told to trust tokens from this one repository's `production` environment, and the GitHub identity can only change the Gleaner resource group.

One script sets it up, using the `az` and `gh` logins I already had:

```bash
GITHUB_REPO=s3991213/gleaner ./deploy/setup-github-oidc.sh
```

It:

1. **Creates the identity.** An Entra ID app registration called `gleaner-github-deploy`, with a service principal.
2. **Limits its permissions.** It gets **Contributor on `gleaner-rg` only**. Even if something went wrong in the pipeline, it couldn't touch anything else in the subscription.
3. **Trusts only this repo.** It adds a federated credential with the subject `repo:s3991213/gleaner:environment:production`. Azure only accepts tokens from workflow runs in that repo, in that GitHub environment.
4. **Saves the IDs to GitHub.** The client, tenant and subscription IDs, the resource group and the app name become repository variables. They're identifiers, not secrets, which is why they're variables rather than secrets.

Two gotchas:

- **Propagation delay.** A brand-new service principal can take a few seconds to appear for role assignments, so the script retries that step.
- **The trust rule didn't match.** The first automatic deploy failed with `AADSTS700213: No matching federated identity record found`. GitHub sent the subject as `repo:s3991213@176739961/gleaner@1420144093:environment:production`, which uses the owner and repo **numeric IDs**, not just their names. That form is the default for newer repos, and it means renaming or recreating a repo can't hijack the trust. The script now adds a rule for both forms. After that, re-running just the failed job worked.

**[CAST: casts/04-github-oidc.cast]**

From now on, every push to `main` runs test, build and deploy. The deploy job logs in with `azure/login`, points the app at the image tagged with the commit, and polls `/healthz` until the app answers, allowing for a cold start.

The first automatic deploy rolled out `sha-647ace4` and passed its health check. **[SCREENSHOT: full green pipeline]**

## Step 5: Secure delivery (week 2)

A single "push and it's live" pipeline is fine for a toy. I wanted the pieces a real team would expect: a test gate, separate environments, a human approval for production, an audit trail, one-click rollback and no secrets anywhere.

### The pipeline now

```
push to main
  -> test (ruff, pytest)  +  secret-scan (gitleaks, full history)     <- the test gate
  -> build one image: ghcr.io/s3991213/gleaner:sha-<commit>
  -> deploy to staging       (automatic, verified)
  -> deploy to production    (waits for my approval, verified)
  -> release notes           (prod-sha-<commit>: changes + how to roll back)
```

The image is built **once**, and that exact image is promoted from staging to production. Production never runs anything staging didn't.

**[SCREENSHOT: pipeline graph with production waiting for approval]**

### Environment separation

`gleaner-staging` is a second container app in the same Container Apps environment. It scales to zero too, so it costs nothing while idle. In GitHub, `staging` and `production` are **environments**:

- Both only accept deploys from `main`.
- `production` requires my approval.
- Each has its own `AZURE_CONTAINERAPP_NAME` variable.
- Azure trusts tokens from each environment separately (federated credentials per environment).

**[CAST: casts/05-staging-app.cast] [CAST: casts/08-github-environments.cast]**

### Proving what's live

The image is built with `APP_VERSION=sha-<commit>`, and `/healthz` returns it:

```bash
curl https://gleaner.livelywater-a1a76a35.australiaeast.azurecontainerapps.io/healthz
# {"status":"ok","version":"sha-xxxxxxx"}
```

A deploy only counts as successful when the new revision is Azure's latest *ready* revision **and** `/healthz` reports the expected version. If that doesn't happen within 5 minutes, the job switches the app back to the image it recorded before starting, and fails.

### Rollback and version notes

- **Rollback button:** Actions, Rollback, Run workflow. Pick an environment and an existing version. Nothing is rebuilt, and production rollbacks still need approval.
- **Release per production deploy:** `prod-sha-xxxxxxx`, listing the commits since the last release, the image, the version it replaced and the exact rollback instructions.
- **Deployment log:** every deploy job writes a summary table (version, previous version, reason, commit, who triggered it, URL, time, result). GitHub's Deployments page keeps the per-environment history.

**[SCREENSHOT: deploy summary table] [SCREENSHOT: release notes]**

### Secrets: there aren't any (in the places that matter)

- **No passwords for deploys.** GitHub logs in to Azure with OIDC, and the IDs it uses are plain variables.
- **Secret scanning.** gitleaks scans the full git history on every push and pull request.
- **Key Vault with managed identity.** Each app has its own system-assigned identity and its own secret, and can read **only that one secret**. The container app config holds a Key Vault *reference*, not the value. The vault lives in a separate resource group, so the deploy identity can't manage it.
- **Nothing sensitive in logs.** More on that below.

**[CAST: casts/06-keyvault.cast]**

### Logging without leaking

The proxy logs one JSON line per request: method, path, status, duration, cache hit or miss, feed host, and a **keyed hash** of the client IP. The hash key is the Key Vault secret, so I can see "the same visitor made 40 requests" without storing who they are. Two things are deliberately never logged:

- **Raw IPs.** uvicorn's access log is off, because it would print them.
- **Full feed URLs or query strings.** Private feeds (Patreon, paid newsletters, some podcast hosts) put access tokens in the URL.

Tests enforce both: a request with `X-Forwarded-For: 203.0.113.77` and `?token=s3cr3t-token` must not produce a log line containing either.

```json
{"ts": "...", "level": "info", "logger": "gleaner.request", "msg": "request", "version": "sha-xxxxxxx",
 "method": "GET", "path": "/api/feed", "status": 400, "duration_ms": 0.8,
 "client": "9fc3049f6f98669c", "feed_host": "10.0.0.1", "cache": "miss",
 "error": "Destination address is not allowed"}
```

Logs go to a Log Analytics workspace with a **0.1 GB/day hard cap**, so it can't exceed the free 5 GB a month. `docs/RUNBOOK.md` has the KQL queries.

**[CAST: casts/07-logging.cast] [SCREENSHOT: Log Analytics query results]**

### Week 2 snags

- **`MissingSubscriptionRegistration` for `Microsoft.KeyVault`.** New subscriptions must register each resource provider once. The script now does it.
- **`(ServerTimeout)` from `az containerapp env update`.** Azure timed out switching the log destination. Because the script is idempotent, re-running it was the whole fix.
- **Old images can't report a version.** Rolling back to a build from before `/healthz` had a version would have looked "unhealthy". The check accepts "no version reported" as long as the new revision is the latest ready one.

## Step 6: On the phone

**TODO:** Add to Home Screen on iOS and Android.

**[VIDEO: phone screen recording, opening from the home screen]**

## What I'd do next

- An uptime check (for example a free external monitor on `/healthz`, which now accepts HEAD).
- Pull requests with a preview environment, using Container Apps revisions with traffic splitting.
- The podcast transcript search in `docs/ROADMAP.md`. The parser already keeps audio enclosure URLs for it.
