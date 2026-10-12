# HiveMind infrastructure

Everything one HiveMind environment (dev, test, prod) needs in Azure, as Bicep. One resource group per
environment.

| Resource | Purpose |
|---|---|
| Foundry account (AI Services) + project + model deployments | Where the agents run |
| Log Analytics + Application Insights (connected to the project) | Agent tracing, hub logs and audit lines, cost queries |
| Container Registry + Container Apps environment + **hub** app | The HiveMind hub: MCP server for agents, plus the task runner |
| Managed identity `id-hivemind-<env>-hub` | Hub runtime: invokes agents, uses memory stores and the SharePoint site |
| Managed identity `id-hivemind-deploy-<env>` + GitHub federated credential | The Deploy agents and Sync rules workflows (no secrets) |
| Foundry connections `appinsights`, `hivemind-hub` | Tracing; how agents authenticate to the hub (agent identity) |

Entra and SharePoint permissions need tenant-level rights that a deployment identity shouldn't have, so they are
scripted for an administrator to run once (`scripts/entra-setup.sh`).

## First-time setup (per environment)

| # | Who | Step |
|---|---|---|
| 1 | Subscription owner | `scripts/bootstrap.sh prod australiaeast <owner/repo>` creates the resource group and the infra deploy identity, and prints GitHub variables. |
| 2 | Entra application admin | `scripts/entra-setup.sh app prod` creates the hub's app registration and prints `HIVEMIND_HUB_AUDIENCE`. |
| 3 | Platform admin | In GitHub, create environments `dev` and `prod` (add required reviewers to `prod`) and set the variables from steps 1 and 2. |
| 4 | Platform admin | Run the **Infrastructure** workflow: `what-if`, then `deploy`. The run summary lists the remaining variables (`AZURE_CLIENT_ID`, `FOUNDRY_ENDPOINT_PROD`, `HIVEMIND_HUB_URL_PROD`…). Set them on the environment. |
| 5 | SharePoint + privileged role admin | Create the HiveMind site (a team site), then `scripts/entra-setup.sh sharepoint prod <site> <hubClientId> <deployClientId>`. This grants access to that site only. |
| 6 | Platform admin | In the Foundry project, create a connection for each system in the catalog (Fabric MCP, SharePoint grounding, AI Search, the ERP's MCP server, Work IQ…), named as in `catalog/` (or mapped via `variables`). |
| 7 | Platform admin | Run **Deploy agents** with apply. This deploys the agents and provisions the SharePoint site's libraries, folders and task list. |
| 8 | Platform admin | `hivemind publish <agent> -e prod` for chat agents (needs **Azure AI Project Manager**), then `hivemind identities -e prod` to grant published agents' new identities access to downstream systems. |

After that, day-to-day changes are pull requests: agents and catalog changes go through **Deploy agents**, and
platform changes through **Infrastructure**.

## Parameters

`main.<env>.bicepparam` holds per-environment choices: model deployments (check regional availability), hub
replica counts, and environment-variable inputs (`GITHUB_REPOSITORY`, `HIVEMIND_HUB_AUDIENCE`,
`HIVEMIND_HUB_IMAGE`) supplied by the workflow.

| Parameter | Default | Notes |
|---|---|---|
| `createFoundry` | `true` | `false` to use an existing Foundry account in the same resource group (`foundryAccountName`). |
| `hubAuth` | `entra` | `key` for a shared-key hub (development only); also set `hub.auth: key` in hivemind.yaml and pass `hubKey`. |
| `deployHubConnection` | `true` | See "Preview caveats". |
| `hubMinReplicas` | 1 | Keep at least 1: the hub also works the task queue. |

## Preview caveats

- **Agent-identity connection.** The `hivemind-hub` connection uses the auth type the Foundry SDK reports for agentic
  identity (`AgenticIdentityToken_Preview`). The published ARM schema doesn't list it yet. If the deployment
  rejects it, deploy with `deployHubConnection=false` and create the connection in the Foundry portal (Remote tool,
  Agent identity, audience = `HIVEMIND_HUB_AUDIENCE`) with the name `hivemind-hub`.
- **Model versions** in the parameter files are examples. Use the versions available in your region.
- **Publishing** to Microsoft 365 needs the Azure AI Project Manager role. It isn't granted to the deploy identity by
  default, so publishing stays a deliberate admin action.

## Validate locally

```bash
az bicep build --file infra/main.bicep
az bicep build-params --file infra/main.prod.bicepparam
az deployment group what-if -g rg-hivemind-dev -f infra/main.bicep -p infra/main.dev.bicepparam
```
