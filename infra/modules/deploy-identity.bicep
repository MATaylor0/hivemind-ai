// The identity GitHub Actions uses to deploy agents (Deploy agents / Sync SharePoint rules workflows).
// Federated credentials: no secrets. Least privilege: Foundry data plane + read access for connections and
// cost queries. Infrastructure changes use a separate, more privileged identity (see scripts/bootstrap.sh).

param location string
param name string
param githubRepository string
param githubEnvironment string
param foundryAccountName string
param appInsightsName string
param tags object

var roles = {
  azureAiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  reader: 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
  monitoringReader: '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

resource github 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: identity
  name: 'github-${githubEnvironment}'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:environment:${githubEnvironment}'
    audiences: ['api://AzureADTokenExchange']
  }
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// Create agent versions, memory stores and routines; publish needs Azure AI Project Manager (grant separately).
resource aiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, identity.id, roles.azureAiUser)
  scope: foundryAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAiUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Resolve project connection names to ids.
resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, identity.id, roles.reader)
  scope: foundryAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.reader)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Usage and cost queries (observability/agent-usage.kql, budget guard).
resource monitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, identity.id, roles.monitoringReader)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.monitoringReader)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output clientId string = identity.properties.clientId
output principalId string = identity.properties.principalId
