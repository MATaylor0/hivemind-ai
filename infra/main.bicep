// HiveMind platform for one environment (dev, test or prod), deployed into one resource group.
//
//   Foundry account + project (+ tracing connection)  the agents, routines, evals and process hosts live here
//   Log Analytics + Application Insights               agent traces, production evals, cost queries
//   Container registry                                 images for process hosts (Agent Framework workflows)
//   Managed identity + GitHub federated credential     the deploy workflows (no secrets)
//
// Everything that runs on Foundry - prompt agents, hosted process agents, routines, eval configurations and model
// deployments - is declared in the azure.yaml that `hivemind build` generates and deployed by azd. This template
// only provides the platform underneath. Entra and SharePoint grants are tenant-admin steps: see infra/README.md.

targetScope = 'resourceGroup'

@description('Environment name; must match an environment in hivemind.yaml (dev, prod...).')
param environmentName string

@description('Azure region. Choose one where your models and Foundry Agent Service features are available.')
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

@description('GitHub repository (owner/name) allowed to deploy to this environment via OIDC.')
param githubRepository string

@description('GitHub environment the deploy workflows run in.')
param githubEnvironment string = environmentName

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
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    tags: allTags
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

module registry 'modules/registry.bicep' = {
  name: 'registry'
  params: {
    location: location
    namePrefix: '${namePrefix}-${environmentName}'
    deployPrincipalId: deployIdentity.outputs.principalId
    projectPrincipalId: foundry.outputs.projectPrincipalId
    tags: allTags
  }
}

@description('Set as FOUNDRY_ENDPOINT_<ENV>; hivemind.yaml points the environment at this project.')
output projectEndpoint string = foundry.outputs.projectEndpoint
output registryLoginServer string = registry.outputs.loginServer
output deployClientId string = deployIdentity.outputs.clientId
output deployPrincipalId string = deployIdentity.outputs.principalId
output appInsightsName string = monitoring.outputs.appInsightsName

@description('Variables for the GitHub environment used by the Deploy agents workflow.')
output githubVariables object = {
  AZURE_CLIENT_ID: deployIdentity.outputs.clientId
  AZURE_TENANT_ID: tenant().tenantId
  AZURE_SUBSCRIPTION_ID: subscription().subscriptionId
  AZURE_LOCATION: location
  AZURE_CONTAINER_REGISTRY_ENDPOINT: registry.outputs.loginServer
  'FOUNDRY_ENDPOINT_${envUpper}': foundry.outputs.projectEndpoint
}
