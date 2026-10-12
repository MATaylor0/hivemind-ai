using './main.bicep'

param environmentName = 'prod'
param githubRepository = readEnvironmentVariable('GITHUB_REPOSITORY', 'MATaylor0/hivemind-ai')
