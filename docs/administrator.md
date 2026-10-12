# Running HiveMind: the administrator's guide

The administrator (usually a small platform team) runs the agentic workforce: they design the organisation the
agents work in, add agents, deploy them safely and keep an eye on them. Business teams shape the detail in
SharePoint, and each agent has an accountable human owner.

| Person | Owns | Works in |
|---|---|---|
| **Administrator** | Org model: goals, teams, roles, tool catalog, policies, environments. Deployments. | Git repo, `hivemind ui` / CLI, GitHub Actions |
| **Agent owner** | One agent's purpose, behaviour and budget | Pull request reviews (CODEOWNERS) |
| **Business users** | Day-to-day rules, tasks for agents, outputs | SharePoint site, Teams, M365 Copilot |

## 1. Set up the platform (once per environment)

Follow [infra/README.md](../infra/README.md): bootstrap script, then the hub app registration, the Infrastructure
workflow (Foundry, monitoring, hub, identities), SharePoint grants, and Foundry connections for your systems.

## 2. Design the organisation

This is the real work, and it's what makes every later agent cheap. In the org directory:

- `hivemind.yaml`
  - **goals**: what the organisation is trying to achieve
  - **teams**
  - **defaults**: the constitution every agent follows, and shared knowledge
  - **memory scopes**
  - **policies**: owners, budgets, allowed models, reviewers for write tools
  - **environments**: mark prod `protected`
- `catalog/`: every system an agent may touch, once. Set `access: read|write`, `audience` (who may be granted it)
  and `user_context` (acts as the signed-in person). See [m365-capabilities.md](m365-capabilities.md).
- `roles/`: how executives, managers, reviewers and workers behave.

## 3. Add agents

```bash
hivemind ui                     # opens the console in your browser
```

From the console (or a terminal):

```text
new treasury-analyst --role worker --team finance --reports-to finance-manager --owner treasury@contoso.com
render treasury-analyst -e local      # read the exact prompt it will get
validate
```

Edit the spec and job description, then open a pull request. CI validates it and posts the org chart and fleet table.

## 4. Deploy

Merging runs **Deploy agents**: `plan`, then `apply` (new versions only for agents that changed), then
`sharepoint provision` (folders for new agents, task list drop-down). Production needs approval in the GitHub
environment. Then, for chat agents:

```text
publish chief-of-staff -e prod       # into M365 Copilot and Teams (admin action)
identities -e prod                    # published agents get new identities: grant them downstream access
```

The console allows `plan`, `inventory` and `tasks list` against prod, but refuses changes there. Production
changes always go through a pull request.

## 5. Hand over the detail

Point business owners at [business-users.md](business-users.md). They maintain Word documents in their agents'
SharePoint rules folders; the hourly **Sync SharePoint rules** workflow deploys approved edits, and only the
affected agents get new versions.

## 6. Run and govern

| Question | Where |
|---|---|
| What exists, who owns it, what can it touch? | Console: Overview, Agents, Org chart; `inventory` |
| Is prod what git says it should be? | Console: Plan (prod, read-only) |
| What are agents working on? Anything stuck or failing? | Console: Tasks; or the SharePoint task list |
| Why did an agent do that? | Foundry portal traces; hub audit log (`observability/hub-activity.kql`) |
| What is it costing? | `observability/agent-usage.kql` in Application Insights; `budget` for declared budgets |
| Stop an agent now | `pause <agent> -e <env>` from a terminal or CI (prod is protected in the console) |

## The console

`hivemind ui` serves a local web console for the org directory you run it in:

- **Overview**: counts, goals with the agents serving them, policy findings, teams, the SharePoint site.
- **Org chart**: reporting lines, reviewers and collaborators. Select an agent to open it.
- **Agents**: the fleet table. Each agent shows its compiled prompt, tools (write and as-user flagged),
  instruction sources (repo and SharePoint), goals, schedules, Foundry definition and version metadata.
- **Plan**: what `apply` would change in the selected environment.
- **Tasks**: the queue, with instructions, results, errors and output links. In local environments you can create
  a task and work the queue.
- **Console**: runs `hivemind` sub-commands and streams the output. No shell: anything else is refused. Commands
  that change something ask for confirmation, and protected environments are read-only.

It listens on 127.0.0.1 only, behind a per-session token printed at start-up. It uses your own Azure sign-in
(`az login`) for Foundry environments.
