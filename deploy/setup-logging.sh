#!/usr/bin/env bash
# Send container logs to a Log Analytics workspace with a hard daily cap.
#
# Log Analytics includes 5 GB of ingestion per billing account per month and
# 31 days of retention at no charge. The daily cap below (0.1 GB/day, about
# 3 GB/month at most) keeps Gleaner inside that, even under a traffic spike:
# once the cap is hit, ingestion stops until the next day.
#
# Live tailing works with or without this: az containerapp logs show --follow
#
# Usage:
#   ./deploy/setup-logging.sh
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-gleaner-rg}"
LOCATION="${LOCATION:-australiaeast}"
ENVIRONMENT="${ENVIRONMENT:-gleaner-env}"
WORKSPACE="${WORKSPACE:-gleaner-logs}"
DAILY_CAP_GB="${DAILY_CAP_GB:-0.1}"

echo "Creating Log Analytics workspace ${WORKSPACE} (30 day retention, ${DAILY_CAP_GB} GB/day cap)"
if ! az monitor log-analytics workspace show --resource-group "${RESOURCE_GROUP}" \
  --workspace-name "${WORKSPACE}" --output none 2>/dev/null; then
  az monitor log-analytics workspace create \
    --resource-group "${RESOURCE_GROUP}" \
    --workspace-name "${WORKSPACE}" \
    --location "${LOCATION}" \
    --retention-time 30 \
    --quota "${DAILY_CAP_GB}" \
    --output none
else
  az monitor log-analytics workspace update \
    --resource-group "${RESOURCE_GROUP}" \
    --workspace-name "${WORKSPACE}" \
    --retention-time 30 \
    --quota "${DAILY_CAP_GB}" \
    --output none
fi

WORKSPACE_ID="$(az monitor log-analytics workspace show --resource-group "${RESOURCE_GROUP}" \
  --workspace-name "${WORKSPACE}" --query customerId --output tsv)"
# The shared key is only held in this shell variable and passed straight to Azure.
WORKSPACE_KEY="$(az monitor log-analytics workspace get-shared-keys --resource-group "${RESOURCE_GROUP}" \
  --workspace-name "${WORKSPACE}" --query primarySharedKey --output tsv)"

echo "Pointing Container Apps environment ${ENVIRONMENT} at the workspace"
az containerapp env update \
  --name "${ENVIRONMENT}" \
  --resource-group "${RESOURCE_GROUP}" \
  --logs-destination log-analytics \
  --logs-workspace-id "${WORKSPACE_ID}" \
  --logs-workspace-key "${WORKSPACE_KEY}" \
  --output none
unset WORKSPACE_KEY

cat <<EOF
Done. Example query (logs take a few minutes to appear):

az monitor log-analytics query --workspace ${WORKSPACE_ID} --analytics-query '
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "gleaner"
| extend e = parse_json(Log_s)
| where e.msg == "request"
| project TimeGenerated, status = toint(e.status), path = tostring(e.path),
          feed_host = tostring(e.feed_host), ms = todouble(e.duration_ms), version = tostring(e.version)
| order by TimeGenerated desc
| take 20'
EOF
