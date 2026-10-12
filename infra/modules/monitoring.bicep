// Log Analytics + workspace-based Application Insights. Foundry traces agent runs here, including process hosts;
// production trace evals and cost queries read from the same workspace.

param location string
param namePrefix string
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${namePrefix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 90
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${namePrefix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    DisableLocalAuth: false
  }
}

output workspaceId string = workspace.id
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
#disable-next-line outputs-should-not-contain-secrets // connection strings are identifiers, not credentials
output appInsightsConnectionString string = appInsights.properties.ConnectionString
