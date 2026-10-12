// Container registry for process hosts: azd builds each process (an Agent Framework workflow) into an image that
// Foundry runs as a hosted agent. The deploy identity pushes; the Foundry project's identity pulls.

param location string
param namePrefix string
param deployPrincipalId string
param projectPrincipalId string
param tags object

var roles = {
  acrPull: '7f951dca-d4ef-4d55-8b3d-c1ecd8c1d8c0'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  #disable-next-line BCP334
  name: take(replace('cr${namePrefix}${uniqueString(resourceGroup().id)}', '-', ''), 50)
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

resource push 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, deployPrincipalId, roles.acrPush)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPush)
    principalId: deployPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, projectPrincipalId, roles.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
