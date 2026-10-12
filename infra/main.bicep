// HiveMind platform for one environment (dev, test or prod), deployed into one resource group.
//
//   Foundry account + project + model deployments   (agents run here)
//   Log Analytics + Application Insights            (Foundry tracing, hub logs, cost queries)
//   Container Registry + Container Apps             (the HiveMind hub: MCP server + task runner)
//   Managed identities                              (hub runtime; GitHub deploy via OIDC)
//   Foundry connections                             (Application Insights; the hub, via agent identity)
//
// Entra app registration and SharePoint grants need tenant-admin rights, so they are scripted separately:
// see infra/README.md and infra/scripts/entra-setup.sh.

targetScope = 'resourceGroup'

@description('Environment name; must match an environment in hivemind.yaml (dev, prod...).')
param environmentName string

@description('Azure region. Choose one where your models (and Foundry Agent Service preview features) are available.')
param location string = resourceGroup().location

@description('Short prefix for resource names.')
@maxLength(12)
param namePrefix string = 'hivemind'

@description('Create the Foundry account and project. Set false to use an existing account in this resource group.')
param createFoundry bool = true

@description('Foundry (AI Services) account name. Also its custom subdomain, so it must be globally unique.')
param foundryAccountName string = '${namePrefix}-${environmentName}-${uniqueString(resourceGroup().id)}'

@description('Foundry project name.')
param projectName string = 'hivemind'

@description('Model deployments: [{ name, model, version, sku?, capacity? }]. Names are what hivemind.yaml refers to.')
param modelDeployments array = []

@description('Hub container image. The placeholder is replaced by the infra workflow after the first image build.')
param hubImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Expected audience of agent identity tokens: the hub app registration\'s identifier URI (api://<appId>).')
param hubAudience string

@allowed(['entra', 'key'])
@description('How agents authenticate to the hub: their Entra agent identity (production) or a shared key (dev). Must match hub.auth in hivemind.yaml.')
param hubAuth string = 'entra'

@secure()
@description('Shared hub key, only for hubAuth=key.')
param hubKey string = ''

@description('Create the hivemind-hub Foundry connection. Set false to create it in the portal instead (see modules/hub-connection.bicep).')
param deployHubConnection bool = true

@description('Entra tenant id.')
param tenantId string = tenant().tenantId

@description('GitHub repository (owner/name) allowed to deploy agents to this environment via OIDC.')
param githubRepository string

@description('GitHub environment the deploy workflows run in.')
param githubEnvironment string = environmentName

@minValue(1)
@description('Keep at least one hub replica: it also runs the task queue.')
param hubMinReplicas int = 1

param hubMaxReplicas int = 3

param tags object = {}

var allTags = union(tags, { workload: 'hivemind', environment: environmentName })
var envUpper = toUpper(environmentName)

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    namePrefix: '${namePrefix}-${environmentName}'
    tags: allTags
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    location: location
    createAccount: createFoundry
    accountName: foundryAccountName
    projectName: projectName
    modelDeployments: modelDeployments
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    tags: allTags
  }
}

module hub 'modules/hub.bicep' = {
  name: 'hub'
  params: {
    location: location
    namePrefix: '${namePrefix}-${environmentName}'
    image: hubImage
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    foundryAccountName: foundry.outputs.accountName
    minReplicas: hubMinReplicas
    maxReplicas: hubMaxReplicas
    hubKey: hubAuth == 'key' ? hubKey : ''
    environmentVariables: [
      { name: 'HIVEMIND_ENV', value: environmentName }
      { name: 'FOUNDRY_ENDPOINT_${envUpper}', value: foundry.outputs.projectEndpoint }
      { name: 'HIVEMIND_HUB_AUDIENCE', value: hubAudience }
      { name: 'AZURE_TENANT_ID', value: tenantId }
      { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.appInsightsConnectionString }
    ]
    tags: allTags
  }
}

module hubConnection 'modules/hub-connection.bicep' = if (deployHubConnection) {
  name: 'hub-connection'
  params: {
    accountName: foundry.outputs.accountName
    projectName: projectName
    hubUrl: hub.outputs.hubUrl
    audience: hubAudience
    auth: hubAuth
    hubKey: hubKey
  }
}

module deployIdentity 'modules/deploy-identity.bicep' = {
  name: 'deploy-identity'
  params: {
    location: location
    name: 'id-${namePrefix}-deploy-${environmentName}'
    githubRepository: githubRepository
    githubEnvironment: githubEnvironment
    foundryAccountName: foundry.outputs.accountName
    appInsightsName: monitoring.outputs.appInsightsName
    tags: allTags
  }
}

@description('Set as FOUNDRY_ENDPOINT_<ENV> in GitHub and wherever the CLI runs.')
output projectEndpoint string = foundry.outputs.projectEndpoint

@description('Set as HIVEMIND_HUB_URL_<ENV>. Agents call the hub here.')
output hubUrl string = hub.outputs.hubUrl

output containerAppName string = hub.outputs.containerAppName
output registryName string = hub.outputs.registryName
output registryLoginServer string = hub.outputs.registryLoginServer

@description('Grant Sites.Selected (write) on the HiveMind site to this identity: infra/scripts/entra-setup.sh.')
output hubPrincipalId string = hub.outputs.principalId
output hubClientId string = hub.outputs.clientId

@description('Set as AZURE_CLIENT_ID in the GitHub environment used by the Deploy agents workflows.')
output deployClientId string = deployIdentity.outputs.clientId
output deployPrincipalId string = deployIdentity.outputs.principalId

output appInsightsName string = monitoring.outputs.appInsightsName
output githubVariables object = {
  AZURE_CLIENT_ID: deployIdentity.outputs.clientId
  AZURE_TENANT_ID: tenantId
  AZURE_SUBSCRIPTION_ID: subscription().subscriptionId
  'FOUNDRY_ENDPOINT_${envUpper}': foundry.outputs.projectEndpoint
  'HIVEMIND_HUB_URL_${envUpper}': hub.outputs.hubUrl
}
