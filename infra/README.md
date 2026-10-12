# HiveMind infrastructure

The platform under one HiveMind environment (dev, test, prod), as Bicep, in one resource group. Everything that runs
*on* Foundry (prompt agents, process hosts, routines, eval configurations, model deployments) is declared in the
`azure.yaml` that `hivemind build` generates and deployed by azd, not here.

| Resource | Purpose |
|---|---|
| Foundry account (AI Services) + project | Where agents, processes, routines and evals live |
| Log Analytics + Application Insights (project tracing connection) | Traces, production trace evals, cost queries |
| Container registry | Images for process hosts (azd builds and pushes; the project identity pulls) |
| Managed identity `id-hivemind-deploy-<env>` + GitHub federated credential | Deploy agents and Sync rules workflows (no secrets) |

## First-time setup (per environment)

| # | Who | Step |
|---|---|---|
| 1 | Subscription owner | `scripts/bootstrap.sh prod australiaeast <owner/repo>` creates the resource group and the infra deploy identity, and prints GitHub variables. |
| 2 | Platform admin | In GitHub, create environments `dev` and `prod` (add required reviewers to `prod`) and set the variables from step 1. |
| 3 | Platform admin | Run the **Infrastructure** workflow: `what-if`, then `deploy`. Set the variables it prints (`AZURE_CLIENT_ID`, `AZURE_LOCATION`, `AZURE_CONTAINER_REGISTRY_ENDPOINT`, `FOUNDRY_ENDPOINT_PROD`…) on the environment. |
| 4 | SharePoint + privileged role admin | Create the HiveMind site, then `scripts/entra-setup.sh sharepoint prod <site> <deployClientId>`. |
| 5 | Platform admin | In the Foundry project, create a connection for each system in the catalog (Fabric MCP, AI Search, ERP MCP, Work IQ…), named as in `catalog/`. |
| 6 | Platform admin | Run **Deploy agents**: dev, eval gate, then prod after approval. |
| 7 | Platform admin | `hivemind publish <agent> -e prod` for chat agents (needs Azure AI Project Manager), then `hivemind identities` to grant published agents' identities downstream access. |

## Notes

- **Roles.** The deploy identity has Azure AI User on the Foundry account (agents, routines, evals, memory stores),
  Reader (connections), Monitoring Reader (cost and usage queries) and AcrPush. If azd needs more rights for hosted
  agents in your tenant, grant Azure AI Project Manager on the project.
- **Existing Foundry.** Set `createFoundry=false` and `foundryAccountName` to use an existing account in the
  resource group.
- **Validate locally:** `az bicep build --file infra/main.bicep` and
  `az deployment group what-if -g rg-hivemind-dev -f infra/main.bicep -p infra/main.dev.bicepparam`.
