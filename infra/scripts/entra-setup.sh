#!/usr/bin/env bash
# Entra and SharePoint steps that need tenant-level rights, so they are not in the Bicep templates.
#
#   ./entra-setup.sh app <environment>
#       Creates the hub's app registration. Its identifier URI is the audience agents request tokens for.
#       Needs: Application Administrator (or Cloud Application Administrator).
#
#   ./entra-setup.sh sharepoint <environment> <site> <hub-client-id> <deploy-client-id>
#       Grants the hub and the deploy identity access to the HiveMind SharePoint site, and nothing else
#       (Graph Sites.Selected + a site-level permission). Client ids come from the infra deployment outputs.
#       Needs: Privileged Role Administrator (app role grant) and SharePoint Administrator (site permission).
#       e.g. ./entra-setup.sh sharepoint prod contoso.sharepoint.com/sites/HiveMind <hubClientId> <deployClientId>
set -euo pipefail

GRAPH_APP_ID=00000003-0000-0000-c000-000000000000
cmd=${1:-}

case "$cmd" in
  app)
    ENV_NAME=${2:?environment}
    NAME="hivemind-hub-$ENV_NAME"
    APP_ID=$(az ad app list --display-name "$NAME" --query "[0].appId" -o tsv)
    if [ -z "$APP_ID" ]; then
      APP_ID=$(az ad app create --display-name "$NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)
      echo "created app registration $NAME ($APP_ID)"
    fi
    az ad app update --id "$APP_ID" --identifier-uris "api://$APP_ID"
    az ad sp show --id "$APP_ID" -o none 2>/dev/null || az ad sp create --id "$APP_ID" -o none
    cat <<OUT

Hub audience: api://$APP_ID
  - GitHub environment '$ENV_NAME' variable HIVEMIND_HUB_AUDIENCE = api://$APP_ID
  - hivemind.yaml hub.audience reads \${env:HIVEMIND_HUB_AUDIENCE}
Next: run the Infrastructure workflow for '$ENV_NAME' (deploy).
OUT
    ;;

  sharepoint)
    ENV_NAME=${2:?environment}
    SITE=${3:?site, e.g. contoso.sharepoint.com/sites/HiveMind}
    HUB_CLIENT_ID=${4:?hub client id (infra output hubClientId)}
    DEPLOY_CLIENT_ID=${5:?deploy client id (infra output deployClientId)}

    GRAPH_SP=$(az ad sp show --id $GRAPH_APP_ID --query id -o tsv)
    SITES_SELECTED=$(az ad sp show --id $GRAPH_APP_ID --query "appRoles[?value=='Sites.Selected'].id | [0]" -o tsv)

    grant_app_role() {  # $1 = client id of a managed identity
      local sp
      sp=$(az ad sp show --id "$1" --query id -o tsv)
      az rest --method POST --url "https://graph.microsoft.com/v1.0/servicePrincipals/$GRAPH_SP/appRoleAssignedTo" \
        --body "{\"principalId\":\"$sp\",\"resourceId\":\"$GRAPH_SP\",\"appRoleId\":\"$SITES_SELECTED\"}" -o none \
        2>/dev/null && echo "  Sites.Selected granted to $1" || echo "  Sites.Selected already granted to $1"
    }

    grant_site() {  # $1 = client id, $2 = display name, $3 = role
      az rest --method POST --url "https://graph.microsoft.com/v1.0/sites/$SITE_ID/permissions" --body \
        "{\"roles\":[\"$3\"],\"grantedToIdentities\":[{\"application\":{\"id\":\"$1\",\"displayName\":\"$2\"}}]}" -o none
      echo "  $3 on $SITE for $2"
    }

    HOST=${SITE%%/*}
    SITE_PATH=${SITE#*/}
    SITE_ID=$(az rest --method GET --url "https://graph.microsoft.com/v1.0/sites/$HOST:/$SITE_PATH" --query id -o tsv)

    echo "Graph application permission (admin consent) for the managed identities"
    grant_app_role "$HUB_CLIENT_ID"
    grant_app_role "$DEPLOY_CLIENT_ID"
    echo "Site permissions on $SITE"
    grant_site "$HUB_CLIENT_ID" "hivemind-hub-$ENV_NAME" write             # tasks, outputs
    grant_site "$DEPLOY_CLIENT_ID" "hivemind-deploy-$ENV_NAME" fullcontrol # rules + `sharepoint provision`
    echo "Next: run 'hivemind sharepoint provision -e $ENV_NAME' (or the Deploy agents workflow with apply)."
    ;;

  *)
    sed -n '2,14p' "$0"
    exit 1
    ;;
esac
