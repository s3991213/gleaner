#!/usr/bin/env bash
# Deploy Gleaner to Azure Container Apps on the consumption (free tier friendly) plan.
#
# Prerequisites:
#   - az CLI logged in (az login) with the containerapp extension (az extension add -n containerapp)
#   - The image already pushed to ghcr.io and set to public visibility, for example:
#       docker build --platform linux/amd64 -t ghcr.io/OWNER/gleaner:latest .
#       docker push ghcr.io/OWNER/gleaner:latest
#
# Usage:
#   IMAGE=ghcr.io/OWNER/gleaner:latest ./deploy/deploy.sh
#
# Override any variable below from the environment. No secrets are needed.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-gleaner-rg}"
LOCATION="${LOCATION:-australiaeast}"
ENVIRONMENT="${ENVIRONMENT:-gleaner-env}"
APP_NAME="${APP_NAME:-gleaner}"
IMAGE="${IMAGE:?Set IMAGE, for example IMAGE=ghcr.io/OWNER/gleaner:latest}"

echo "Registering resource providers (safe to re-run)"
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait

echo "Creating resource group ${RESOURCE_GROUP} in ${LOCATION}"
az group create --name "${RESOURCE_GROUP}" --location "${LOCATION}" --output none

echo "Creating Container Apps environment ${ENVIRONMENT} (no Log Analytics, to avoid cost)"
if ! az containerapp env show --name "${ENVIRONMENT}" --resource-group "${RESOURCE_GROUP}" --output none 2>/dev/null; then
  az containerapp env create \
    --name "${ENVIRONMENT}" \
    --resource-group "${RESOURCE_GROUP}" \
    --location "${LOCATION}" \
    --logs-destination none \
    --output none
fi

if az containerapp show --name "${APP_NAME}" --resource-group "${RESOURCE_GROUP}" --output none 2>/dev/null; then
  echo "Updating container app ${APP_NAME} to ${IMAGE}"
  az containerapp update \
    --name "${APP_NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --image "${IMAGE}" \
    --min-replicas 0 \
    --max-replicas 2 \
    --output none
else
  echo "Creating container app ${APP_NAME} from ${IMAGE}"
  az containerapp create \
    --name "${APP_NAME}" \
    --resource-group "${RESOURCE_GROUP}" \
    --environment "${ENVIRONMENT}" \
    --image "${IMAGE}" \
    --ingress external \
    --target-port 8000 \
    --min-replicas 0 \
    --max-replicas 2 \
    --cpu 0.25 \
    --memory 0.5Gi \
    --output none
fi

FQDN="$(az containerapp show --name "${APP_NAME}" --resource-group "${RESOURCE_GROUP}" \
  --query properties.configuration.ingress.fqdn --output tsv)"
echo "Deployed: https://${FQDN}"
echo "Health check: curl https://${FQDN}/healthz"
