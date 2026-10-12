using './main.bicep'

param environmentName = 'dev'
param githubRepository = readEnvironmentVariable('GITHUB_REPOSITORY', 'MATaylor0/hivemind-ai')
param hubAudience = readEnvironmentVariable('HIVEMIND_HUB_AUDIENCE', 'api://00000000-0000-0000-0000-000000000000')
param hubImage = readEnvironmentVariable('HIVEMIND_HUB_IMAGE', 'mcr.microsoft.com/k8se/quickstart:latest')

// Check model and version availability in your region (Foundry portal > Models).
param modelDeployments = [
  { name: 'gpt-5-mini', model: 'gpt-5-mini', version: '2025-08-07', capacity: 50 }
  { name: 'text-embedding-3-small', model: 'text-embedding-3-small', version: '1', sku: 'Standard', capacity: 50 }
]

param hubMinReplicas = 1
param hubMaxReplicas = 2
