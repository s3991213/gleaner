#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy to Azure without a stored password,
# with separate staging and production environments.
#
# Azure side:
#   - Entra ID app registration with Contributor on the Gleaner resource group only
#   - Federated credentials that trust only workflow runs from this repo's
#     "staging" and "production" GitHub environments
# GitHub side:
#   - Environments "staging" and "production", both limited to the main branch
#   - Production requires approval from REVIEWER (default: you) before it deploys
#   - Repository variables for the Azure IDs (identifiers, not secrets) and one
#     environment variable per environment naming its container app
#
# Run after deploy/deploy.sh has created both apps. Needs: az login, gh auth login.
#
# Usage:
#   GITHUB_REPO=OWNER/gleaner ./deploy/setup-github-oidc.sh
set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:?Set GITHUB_REPO, for example GITHUB_REPO=OWNER/gleaner}"
RESOURCE_GROUP="${RESOURCE_GROUP:-gleaner-rg}"
SP_NAME="${SP_NAME:-gleaner-github-deploy}"
STAGING_APP="${STAGING_APP:-gleaner-staging}"
PRODUCTION_APP="${PRODUCTION_APP:-gleaner}"
REVIEWER="${REVIEWER:-$(gh api user --jq .login)}"

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

# GitHub may send the subject with names only (older repos) or with immutable
# owner and repo IDs (repo:OWNER@ID/REPO@ID:...). Trust both forms of this repo.
OWNER_ID="$(gh api "repos/${GITHUB_REPO}" --jq .owner.id)"
REPO_ID="$(gh api "repos/${GITHUB_REPO}" --jq .id)"
ID_REPO="${GITHUB_REPO%%/*}@${OWNER_ID}/${GITHUB_REPO##*/}@${REPO_ID}"

add_credential() {
  local name="$1" subject="$2"
  if ! az ad app federated-credential list --id "${CLIENT_ID}" --query "[?name=='${name}']" --output tsv | grep -q .; then
    az ad app federated-credential create --id "${CLIENT_ID}" --parameters "{
      \"name\": \"${name}\",
      \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"${subject}\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }" --output none
  fi
}

REVIEWER_ID="$(gh api "users/${REVIEWER}" --jq .id)"

setup_environment() {
  local env="$1" app="$2" needs_approval="$3"
  echo "Environment ${env} -> container app ${app}"

  echo "  Trusting GitHub Actions runs from ${GITHUB_REPO} (environment: ${env})"
  add_credential "github-${env}" "repo:${GITHUB_REPO}:environment:${env}"
  add_credential "github-${env}-ids" "repo:${ID_REPO}:environment:${env}"

  local reviewers='[]'
  if [[ "${needs_approval}" == yes ]]; then
    reviewers="[{\"type\": \"User\", \"id\": ${REVIEWER_ID}}]"
    echo "  Requiring approval from ${REVIEWER}"
  fi
  gh api --method PUT "repos/${GITHUB_REPO}/environments/${env}" --input - --silent <<EOF
{
  "reviewers": ${reviewers},
  "prevent_self_review": false,
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
EOF
  echo "  Allowing deploys from main only"
  if ! gh api "repos/${GITHUB_REPO}/environments/${env}/deployment-branch-policies" \
    --jq '.branch_policies[].name' | grep -qx main; then
    gh api --method POST "repos/${GITHUB_REPO}/environments/${env}/deployment-branch-policies" \
      -f name=main -f type=branch --silent
  fi
  gh variable set AZURE_CONTAINERAPP_NAME --repo "${GITHUB_REPO}" --env "${env}" --body "${app}"
}

setup_environment staging "${STAGING_APP}" no
setup_environment production "${PRODUCTION_APP}" yes

echo "Saving shared IDs as GitHub repository variables"
gh variable set AZURE_CLIENT_ID --repo "${GITHUB_REPO}" --body "${CLIENT_ID}"
gh variable set AZURE_TENANT_ID --repo "${GITHUB_REPO}" --body "${TENANT_ID}"
gh variable set AZURE_SUBSCRIPTION_ID --repo "${GITHUB_REPO}" --body "${SUBSCRIPTION_ID}"
gh variable set AZURE_RESOURCE_GROUP --repo "${GITHUB_REPO}" --body "${RESOURCE_GROUP}"
# The app name now lives on each environment; drop the old repository-wide one.
gh variable delete AZURE_CONTAINERAPP_NAME --repo "${GITHUB_REPO}" 2>/dev/null || true

echo "Done. Pushes to main deploy to staging, then wait for approval before production."
