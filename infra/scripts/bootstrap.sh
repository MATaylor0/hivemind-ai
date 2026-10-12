#!/usr/bin/env bash
# One-time, per environment, by a subscription owner: the resource group and the identity GitHub uses to deploy
# infrastructure (infra workflow). Everything else is created by infra/main.bicep.
#
#   ./bootstrap.sh <environment> <location> <github-owner/repo> [resource-group]
#   ./bootstrap.sh prod australiaeast contoso/hivemind-org
set -euo pipefail

ENV_NAME=${1:?environment, e.g. dev}
LOCATION=${2:?Azure region, e.g. australiaeast}
REPO=${3:?GitHub repository, owner/name}
RG=${4:-rg-hivemind-$ENV_NAME}
IDENTITY=id-hivemind-infra-$ENV_NAME

echo "Resource group $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" --tags workload=hivemind environment="$ENV_NAME" -o none

echo "Infrastructure deploy identity $IDENTITY"
az identity create --name "$IDENTITY" --resource-group "$RG" --location "$LOCATION" -o none
CLIENT_ID=$(az identity show -n "$IDENTITY" -g "$RG" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -n "$IDENTITY" -g "$RG" --query principalId -o tsv)

az identity federated-credential create --name "github-$ENV_NAME" --identity-name "$IDENTITY" --resource-group "$RG" \
  --issuer https://token.actions.githubusercontent.com --subject "repo:$REPO:environment:$ENV_NAME" \
  --audiences api://AzureADTokenExchange -o none 2>/dev/null || echo "  (federated credential already exists)"

RG_ID=$(az group show -n "$RG" --query id -o tsv)
# Contributor to create resources; RBAC Administrator for the role assignments in the templates.
for ROLE in "Contributor" "Role Based Access Control Administrator"; do
  az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
    --role "$ROLE" --scope "$RG_ID" -o none
done

cat <<OUT

Done. In GitHub (Settings > Environments > $ENV_NAME > Variables) set:
  AZURE_INFRA_CLIENT_ID   = $CLIENT_ID
  AZURE_TENANT_ID         = $(az account show --query tenantId -o tsv)
  AZURE_SUBSCRIPTION_ID   = $(az account show --query id -o tsv)
  HIVEMIND_RESOURCE_GROUP = $RG
Next: run the Infrastructure workflow for $ENV_NAME (deploy).
OUT
