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

## Step 5: On the phone

**TODO:** Add to Home Screen on iOS and Android.

**[VIDEO: phone screen recording, opening from the home screen]**

## What I'd do next

**TODO**
