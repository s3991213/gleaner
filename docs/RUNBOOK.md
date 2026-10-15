# Runbook

| | URL |
|---|---|
| Production | https://gleaner.livelywater-a1a76a35.australiaeast.azurecontainerapps.io |
| Staging | https://gleaner-staging.livelywater-a1a76a35.australiaeast.azurecontainerapps.io |

## Which version is running?

```bash
curl https://gleaner.livelywater-a1a76a35.australiaeast.azurecontainerapps.io/healthz
# {"status":"ok","version":"sha-1a2b3c4"}
```

`sha-1a2b3c4` is the image tag and the first 7 characters of the commit.

Or: GitHub, Code, **Deployments** (right sidebar) shows the history per environment. Production versions are also listed under **Releases**.

## Ship a change

1. Push to `main` (or merge a pull request).
2. CI runs lint, tests and the secret scan. If any fail, nothing is built or deployed.
3. The image deploys to staging automatically. Check the staging URL.
4. GitHub emails you that production is waiting. Open the run, click **Review deployments**, tick `production`, then **Approve and deploy**.
5. A release `prod-sha-xxxxxxx` is published with the changes and the previous version.

Each deploy job writes a summary (version, previous version, who triggered it, URL, result) at the bottom of its run page. That summary, the Deployments page and the Releases list are the deployment log.

## Roll back

**From GitHub (preferred):** Actions, **Rollback**, Run workflow. Pick the environment, enter the version to return to (the "Replaced" or "Previous version" value from the release or deploy summary), and a reason. Production rollbacks need approval like any production deploy. No rebuild happens; the existing image is reused.

**Automatic:** if a new revision does not become ready and healthy within 5 minutes, the deploy job switches the app back to the image that was running before and the run fails.

**From a terminal (if GitHub is unavailable):**

```bash
az containerapp update -n gleaner -g gleaner-rg --image ghcr.io/s3991213/gleaner:sha-1a2b3c4
```

## Logs

Live tail (always available, no cost):

```bash
az containerapp logs show -n gleaner -g gleaner-rg --follow --format text
```

Query recent requests in Log Analytics (a few minutes behind):

```bash
WS=$(az monitor log-analytics workspace show -g gleaner-rg -n gleaner-logs --query customerId -o tsv)
az monitor log-analytics query --workspace "$WS" --analytics-query '
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "gleaner"
| extend e = parse_json(Log_s)
| where e.msg == "request"
| summarize requests = count(), errors = countif(toint(e.status) >= 500), p95_ms = percentile(todouble(e.duration_ms), 95) by bin(TimeGenerated, 1h)
| order by TimeGenerated desc' -o table
```

Each request log line has: method, path, status, duration_ms, a keyed hash of the client IP (`client`), and for feeds the host, cache hit or miss and any error. It never has the raw IP, the query string or the full feed URL, because private feed URLs often contain tokens.

## Rotate the log hashing key

```bash
KV=$(az keyvault list -g gleaner-secrets-rg --query '[0].name' -o tsv)
openssl rand -hex 32 | tr -d '\n' > /tmp/k && az keyvault secret set --vault-name "$KV" -n log-hash-key-gleaner --file /tmp/k -o none && rm /tmp/k
```

The app picks up the new version within 30 minutes, or immediately on the next deploy. Client hashes before and after rotation will not match, which is the point.

## Costs

Budget alert `gleaner-5-dollar-budget` emails at $1 actual, $5 actual and $5 forecast per month. If it fires, check Cost Management, cost analysis, grouped by resource. The usual suspects would be heavy traffic keeping replicas awake, or log ingestion (capped at 0.1 GB/day).
