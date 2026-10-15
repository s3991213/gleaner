#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy to Azure without a stored password.
#
# Creates an Entra ID app registration with a federated credential that trusts
# only workflow runs from the "production" environment of this GitHub repo, and
# gives it Contributor on the Gleaner resource group only. Then stores the IDs as
# GitHub repository variables (they are identifiers, not secrets).
#
# Run after deploy/deploy.sh has created the resource group and container app.
# Needs: az login, gh auth login.
#
# Usage:
#   GITHUB_REPO=OWNER/gleaner ./deploy/setup-github-oidc.sh
set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:?Set GITHUB_REPO, for example GITHUB_REPO=OWNER/gleaner}"
RESOURCE_GROUP="${RESOURCE_GROUP:-gleaner-rg}"
APP_NAME="${APP_NAME:-gleaner}"
SP_NAME="${SP_NAME:-gleaner-github-deploy}"

SUBSCRIPTION_ID="$(az account show --query id --output tsv)"
TENANT_ID="$(az account show --query tenantId --output tsv)"
RG_ID="$(az group show --name "${RESOURCE_GROUP}" --query id --output tsv)"

echo "Creating app registration ${SP_NAME}"
CLIENT_ID="$(az ad app list --display-name "${SP_NAME}" --query '[0].appId' --output tsv)"
if [[ -z "${CLIENT_ID}" ]]; then
  CLIENT_ID="$(az ad app create --display-name "${SP_NAME}" --query appId --output tsv)"
fi
if ! az ad sp show --id "${CLIENT_ID}" --output none 2>/dev/null; then
  az ad sp create --id "${CLIENT_ID}" --output none
fi

echo "Granting Contributor on resource group ${RESOURCE_GROUP} only"
SP_OBJECT_ID="$(az ad sp show --id "${CLIENT_ID}" --query id --output tsv)"
# A new service principal can take a little while to become visible to role assignments.
for attempt in 1 2 3 4 5 6; do
  if az role assignment create \
    --assignee-object-id "${SP_OBJECT_ID}" \
    --assignee-principal-type ServicePrincipal \
    --role Contributor \
    --scope "${RG_ID}" \
    --output none; then
    break
  fi
  if [[ "${attempt}" == 6 ]]; then
    echo "Role assignment failed" >&2
    exit 1
  fi
  echo "Waiting for the new identity to propagate (attempt ${attempt})"
  sleep 10
done

echo "Trusting GitHub Actions runs from ${GITHUB_REPO} (environment: production)"
if ! az ad app federated-credential list --id "${CLIENT_ID}" --query "[?name=='github-production']" --output tsv | grep -q .; then
  az ad app federated-credential create --id "${CLIENT_ID}" --parameters "{
    \"name\": \"github-production\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:${GITHUB_REPO}:environment:production\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" --output none
fi

echo "Saving IDs as GitHub repository variables"
gh variable set AZURE_CLIENT_ID --repo "${GITHUB_REPO}" --body "${CLIENT_ID}"
gh variable set AZURE_TENANT_ID --repo "${GITHUB_REPO}" --body "${TENANT_ID}"
gh variable set AZURE_SUBSCRIPTION_ID --repo "${GITHUB_REPO}" --body "${SUBSCRIPTION_ID}"
gh variable set AZURE_RESOURCE_GROUP --repo "${GITHUB_REPO}" --body "${RESOURCE_GROUP}"
gh variable set AZURE_CONTAINERAPP_NAME --repo "${GITHUB_REPO}" --body "${APP_NAME}"

echo "Done. The next push to main will deploy automatically."
