// The HiveMind hub on Azure Container Apps: the MCP server agents call (delegate, escalate, review, memory,
// outputs) and the task runner that works the queue. Stateless and horizontally scalable; at least one replica
// stays up because the task runner polls the queue.

param location string
param namePrefix string
param image string
param logAnalyticsWorkspaceId string
param foundryAccountName string
param minReplicas int
param maxReplicas int
param environmentVariables array
@secure()
@description('Only for auth=key: the shared hub key, stored as a Container App secret.')
param hubKey string = ''
param tags object

var roles = {
  acrPull: '7f951dca-d4ef-4d55-8b3d-c1ecd8c1d8c0'
  azureAiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
}
var registryName = take(replace('cr${namePrefix}${uniqueString(resourceGroup().id)}', '-', ''), 50)
var appName = take('ca-${namePrefix}-hub', 32)

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${namePrefix}-hub'
  location: location
  tags: tags
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  #disable-next-line BCP334
  name: registryName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

// Invoke agents (Responses API) and read/write memory stores.
resource foundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, identity.id, roles.azureAiUser)
  scope: foundryAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAiUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsWorkspaceId, '/'))
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${namePrefix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

var placeholder = startsWith(image, 'mcr.microsoft.com/k8se/quickstart')

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: placeholder ? 80 : 8080
        transport: 'auto'
        allowInsecure: false
      }
      secrets: empty(hubKey) ? [] : [
        { name: 'hub-key', value: hubKey }
      ]
      registries: placeholder ? [] : [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'hub'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(environmentVariables, [
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // DefaultAzureCredential -> this identity
            { name: 'PORT', value: '8080' }
          ], empty(hubKey) ? [] : [
            { name: 'HIVEMIND_HUB_KEY', secretRef: 'hub-key' }
          ])
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
  dependsOn: [acrPull]
}

output hubUrl string = 'https://${app.properties.configuration.ingress.fqdn}/mcp'
output containerAppName string = app.name
output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
