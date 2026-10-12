#!/usr/bin/env bash
# SharePoint access for HiveMind, which needs tenant-level rights so it is not in the Bicep templates.
#
#   ./entra-setup.sh sharepoint <environment> <site> <deploy-client-id>
#       Grants the deploy identity access to the HiveMind SharePoint site only (Graph Sites.Selected plus a
#       site-level permission), so deployments can read business-owned rules and `hivemind sharepoint provision`
#       can create the rules folders. The client id is the infra deployment output deployClientId.
#       Needs: Privileged Role Administrator (app role grant) and SharePoint Administrator (site permission).
#       e.g. ./entra-setup.sh sharepoint prod contoso.sharepoint.com/sites/HiveMind <deployClientId>
set -euo pipefail

GRAPH_APP_ID=00000003-0000-0000-c000-000000000000
cmd=${1:-}

case "$cmd" in
  sharepoint)
    ENV_NAME=${2:?environment}
    SITE=${3:?site, e.g. contoso.sharepoint.com/sites/HiveMind}
    DEPLOY_CLIENT_ID=${4:?deploy client id (infra output deployClientId)}

    GRAPH_SP=$(az ad sp show --id $GRAPH_APP_ID --query id -o tsv)
    SITES_SELECTED=$(az ad sp show --id $GRAPH_APP_ID --query "appRoles[?value=='Sites.Selected'].id | [0]" -o tsv)
    SP=$(az ad sp show --id "$DEPLOY_CLIENT_ID" --query id -o tsv)
    az rest --method POST --url "https://graph.microsoft.com/v1.0/servicePrincipals/$GRAPH_SP/appRoleAssignedTo" \
      --body "{\"principalId\":\"$SP\",\"resourceId\":\"$GRAPH_SP\",\"appRoleId\":\"$SITES_SELECTED\"}" -o none \
      2>/dev/null && echo "Sites.Selected granted" || echo "Sites.Selected already granted"

    HOST=${SITE%%/*}
    SITE_PATH=${SITE#*/}
    SITE_ID=$(az rest --method GET --url "https://graph.microsoft.com/v1.0/sites/$HOST:/$SITE_PATH" --query id -o tsv)
    az rest --method POST --url "https://graph.microsoft.com/v1.0/sites/$SITE_ID/permissions" --body \
      "{\"roles\":[\"fullcontrol\"],\"grantedToIdentities\":[{\"application\":{\"id\":\"$DEPLOY_CLIENT_ID\",\"displayName\":\"hivemind-deploy-$ENV_NAME\"}}]}" \
      -o none
    echo "fullcontrol on $SITE for hivemind-deploy-$ENV_NAME (rules library + provisioning)"
    echo "Next: hivemind sharepoint provision -e $ENV_NAME"
    ;;

  *)
    sed -n '2,10p' "$0"
    exit 1
    ;;
esac
