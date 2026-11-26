#!/usr/bin/env bash
# One-time setup, run in Azure Cloud Shell (Bash) by someone with Owner on the subscription.
# Creates, at no cost:
#   rg-abelzeta-portal  - where the portal server, Key Vault and backups will be deployed
#   rg-abelzeta-deploy  - holds the GitHub deployment identity (kept apart so a deployment can't delete it)
#   id-abelzeta-github-deploy - a managed identity GitHub Actions signs in as. No password or key exists:
#       Azure trusts GitHub's sign-in token, only for this repository and branch.
#       It gets Contributor on rg-abelzeta-portal only.
# Undo: az group delete -n rg-abelzeta-deploy   (and rg-abelzeta-portal)
set -euo pipefail

LOCATION=${LOCATION:-eastus2}
RG=rg-abelzeta-portal
ID_RG=rg-abelzeta-deploy
ID_NAME=id-abelzeta-github-deploy
REPO=User123-dev1/mftapp
BRANCH=claude/service-desk-tickets-xypu1p

echo "Subscription and account in use:"
az account show --query "{subscription:name, id:id, signedInAs:user.name}" -o table
echo "Region: $LOCATION"

echo "Registering resource providers (first time can take a minute)..."
for p in Microsoft.Compute Microsoft.Network Microsoft.KeyVault Microsoft.RecoveryServices Microsoft.ManagedIdentity; do
  az provider register --namespace "$p" -o none
done

az group create -n "$RG" -l "$LOCATION" --tags app=abelzeta-portal -o none
az group create -n "$ID_RG" -l "$LOCATION" --tags app=abelzeta-portal -o none
az identity create -g "$ID_RG" -n "$ID_NAME" -o none

CLIENT_ID=$(az identity show -g "$ID_RG" -n "$ID_NAME" --query clientId -o tsv)
PRINCIPAL_ID=$(az identity show -g "$ID_RG" -n "$ID_NAME" --query principalId -o tsv)

# Trust GitHub Actions runs from this repository's branch only. (Matching is case-sensitive,
# so the owner name is registered as GitHub shows it and in lower case.)
n=0
for repo in "$REPO" "${REPO,,}"; do
  n=$((n + 1))
  az identity federated-credential create -g "$ID_RG" --identity-name "$ID_NAME" -n "github-branch-$n" \
    --issuer https://token.actions.githubusercontent.com \
    --subject "repo:$repo:ref:refs/heads/$BRANCH" \
    --audiences api://AzureADTokenExchange -o none 2>/dev/null || true
done
echo "GitHub may sign in as this identity from:"
az identity federated-credential list -g "$ID_RG" --identity-name "$ID_NAME" --query "[].subject" -o tsv

echo "Granting Contributor on $RG only (may retry while the identity propagates)..."
SCOPE=$(az group show -n "$RG" --query id -o tsv)
for i in 1 2 3 4 5 6; do
  if [ "$i" -lt 6 ]; then
    az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
      --role Contributor --scope "$SCOPE" -o none 2>/dev/null && break
    sleep 15
  else   # last try: show Azure's own error message
    az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
      --role Contributor --scope "$SCOPE" -o none && break
    echo
    echo "Role assignment failed (Azure's message is above). Your roles on this subscription:"
    az role assignment list --assignee "$(az ad signed-in-user show --query id -o tsv)" --all \
      --query "[].{role:roleDefinitionName, scope:scope}" -o table || true
    echo "You need Owner (or User Access Administrator) on the subscription. Re-run this script afterwards."
    exit 1
  fi
done

echo
echo "Done. Add these in GitHub: repo Settings > Secrets and variables > Actions > Variables tab:"
echo "  AZURE_CLIENT_ID        $CLIENT_ID"
echo "  AZURE_TENANT_ID        $(az account show --query tenantId -o tsv)"
echo "  AZURE_SUBSCRIPTION_ID  $(az account show --query id -o tsv)"
echo "  AZURE_LOCATION         $LOCATION"
echo "(These are identifiers, not secrets.)"
