#!/usr/bin/env bash
# Managed-secret pattern: each container app reads its own secret from Key Vault
# with its own managed identity. No secret value is stored in GitHub, in the repo,
# in container app config or in logs.
#
# What it creates:
#   - Resource group gleaner-secrets-rg, kept separate from gleaner-rg so the
#     GitHub deploy identity (Contributor on gleaner-rg only) cannot manage the vault
#   - Key Vault (RBAC mode, standard tier: no monthly fee, about $0.03 per 10,000 reads)
#   - One random secret per app: log-hash-key-<app>, used to hash client IPs in logs
#   - A system-assigned managed identity on each app, allowed to read only its own secret
#   - A container app secret that references Key Vault, exposed as env var LOG_HASH_KEY
#
# Usage (after deploy.sh has created the apps):
#   ./deploy/setup-keyvault.sh
# Rotate a key: delete or update the secret in Key Vault; apps pick up the latest
# version within 30 minutes or on the next revision.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-gleaner-rg}"
KV_RESOURCE_GROUP="${KV_RESOURCE_GROUP:-gleaner-secrets-rg}"
LOCATION="${LOCATION:-australiaeast}"
APPS="${APPS:-gleaner gleaner-staging}"

SUBSCRIPTION_ID="$(az account show --query id --output tsv)"
# Vault names are global, so derive a stable suffix from the subscription.
KV_NAME="${KV_NAME:-gleaner-kv-$(printf '%s' "${SUBSCRIPTION_ID}" | shasum | cut -c1-6)}"

retry() {
  local attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if "$@"; then return 0; fi
    echo "  waiting for permissions to propagate (attempt ${attempt})"
    sleep 15
  done
  return 1
}

echo "Registering the Key Vault resource provider (first time only, safe to re-run)"
az provider register --namespace Microsoft.KeyVault --wait

echo "Creating resource group ${KV_RESOURCE_GROUP}"
az group create --name "${KV_RESOURCE_GROUP}" --location "${LOCATION}" --output none

echo "Creating Key Vault ${KV_NAME} (RBAC authorization)"
if ! az keyvault show --name "${KV_NAME}" --output none 2>/dev/null; then
  az keyvault create \
    --name "${KV_NAME}" \
    --resource-group "${KV_RESOURCE_GROUP}" \
    --location "${LOCATION}" \
    --enable-rbac-authorization true \
    --retention-days 7 \
    --output none
fi
KV_ID="$(az keyvault show --name "${KV_NAME}" --query id --output tsv)"
KV_URI="$(az keyvault show --name "${KV_NAME}" --query properties.vaultUri --output tsv)"

echo "Allowing you (the signed-in user) to write secrets in this vault"
ME="$(az ad signed-in-user show --query id --output tsv)"
if [[ -z "$(az role assignment list --assignee "${ME}" --role "Key Vault Secrets Officer" --scope "${KV_ID}" --query '[0].id' --output tsv)" ]]; then
  az role assignment create --assignee-object-id "${ME}" --assignee-principal-type User \
    --role "Key Vault Secrets Officer" --scope "${KV_ID}" --output none
fi

for APP in ${APPS}; do
  SECRET_NAME="log-hash-key-${APP}"
  echo "== ${APP}"

  echo "  Creating secret ${SECRET_NAME} if missing (value is generated locally and never printed)"
  if ! az keyvault secret show --vault-name "${KV_NAME}" --name "${SECRET_NAME}" --output none 2>/dev/null; then
    secret_file="$(mktemp)"
    chmod 600 "${secret_file}"
    openssl rand -hex 32 | tr -d '\n' > "${secret_file}"
    retry az keyvault secret set --vault-name "${KV_NAME}" --name "${SECRET_NAME}" \
      --file "${secret_file}" --encoding utf-8 --output none
    rm -f "${secret_file}"
  fi

  echo "  Giving ${APP} a system-assigned managed identity"
  PRINCIPAL_ID="$(az containerapp identity assign --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
    --system-assigned --query principalId --output tsv)"

  echo "  Allowing that identity to read only ${SECRET_NAME}"
  SECRET_SCOPE="${KV_ID}/secrets/${SECRET_NAME}"
  if [[ -z "$(az role assignment list --assignee "${PRINCIPAL_ID}" --scope "${SECRET_SCOPE}" --query '[0].id' --output tsv)" ]]; then
    retry az role assignment create --assignee-object-id "${PRINCIPAL_ID}" \
      --assignee-principal-type ServicePrincipal \
      --role "Key Vault Secrets User" --scope "${SECRET_SCOPE}" --output none
  fi

  echo "  Referencing the Key Vault secret from the app and exposing it as LOG_HASH_KEY"
  retry az containerapp secret set --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
    --secrets "log-hash-key=keyvaultref:${KV_URI}secrets/${SECRET_NAME},identityref:system" \
    --output none
  az containerapp update --name "${APP}" --resource-group "${RESOURCE_GROUP}" \
    --set-env-vars "LOG_HASH_KEY=secretref:log-hash-key" --output none
done

echo "Done. Vault: ${KV_NAME}"
