using './main.bicep'

param environmentName = 'dev'
param githubRepository = readEnvironmentVariable('GITHUB_REPOSITORY', 'MATaylor0/hivemind-ai')
