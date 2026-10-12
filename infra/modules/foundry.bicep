// Microsoft Foundry: an AI Services account with project management enabled, one project, and the Application
// Insights connection that turns on agent tracing. Model deployments are declared per environment in hivemind.yaml
// and upserted by azd (azure.ai.project), so they are not duplicated here.

param location string
param createAccount bool
param accountName string
param projectName string
param appInsightsId string
@secure()
param appInsightsConnectionString string
param tags object

resource newAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = if (createAccount) {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true // Entra ID only: no API keys
  }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'HiveMind'
    description: 'Agents managed by HiveMind (agents-as-code).'
  }
  dependsOn: [newAccount]
}


resource tracing 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsightsId
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: appInsightsConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
    }
  }
}

output accountName string = accountName
output accountId string = account.id
output projectName string = project.name
output projectPrincipalId string = project.identity.principalId
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${project.name}'
