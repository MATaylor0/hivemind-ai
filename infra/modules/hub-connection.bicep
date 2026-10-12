// The Foundry project connection every HiveMind agent uses to call the hub.
//
// auth 'entra' (production): the agent's own Microsoft Entra identity. Foundry obtains a token for `audience` as
// the calling agent and the hub validates it. Published agents have their own identity; unpublished agents share
// the project's. The ARM schema doesn't list this auth type yet; the value below is the credential type the
// Foundry SDK reports (AgenticIdentityToken_Preview). If a deployment rejects it, set deployHubConnection=false
// and create the connection in the portal (Connected resources > New > Remote tool > Agent identity) with the
// name 'hivemind-hub'.
//
// auth 'key' (development): a custom-keys connection; Foundry sends x-hivemind-key on every hub call.

param accountName string
param projectName string
param hubUrl string
param audience string
@allowed(['entra', 'key'])
param auth string
@secure()
param hubKey string = ''

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

resource entraConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (auth == 'entra') {
  parent: project
  name: 'hivemind-hub'
  properties: {
    category: 'RemoteTool'
    target: hubUrl
    #disable-next-line BCP036
    authType: 'AgenticIdentityToken_Preview'
    isSharedToAll: true
    metadata: {
      audience: audience
    }
  }
}

resource keyConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (auth == 'key') {
  parent: project
  name: 'hivemind-hub'
  properties: {
    category: 'CustomKeys'
    target: hubUrl
    authType: 'CustomKeys'
    isSharedToAll: true
    credentials: {
      keys: {
        'x-hivemind-key': hubKey
      }
    }
  }
}

output connectionName string = 'hivemind-hub'
