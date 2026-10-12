# HiveMind AI

**Agents-as-code for Microsoft Foundry.** Define a whole organisation of AI agents (their roles, reporting lines,
tools, knowledge, memory, schedules, channels and budgets) in version-controlled YAML. HiveMind compiles each one
into a Foundry agent, connects them so they can delegate, escalate, request review and share memory, and gives you
one place to see and govern the fleet.

```
chief-of-staff [executive] - Chief of Staff  (team=executive)
├── compliance-reviewer [reviewer] - Compliance Reviewer  (team=risk)
├── finance-manager [manager] - Finance Manager  (team=finance, reviewed by compliance-reviewer)
│   ├── ap-clerk [worker] - Accounts Payable Clerk  (team=finance, reviewed by compliance-reviewer)
│   └── fpa-analyst [worker] - FP&A Analyst  (team=finance)
└── policy-assistant [worker] - Policy Assistant  (team=people)
```

## Why

Copilot Studio is great for single agents built by makers. HiveMind is for an organisation that wants **many**
agents built on **one** framework, with engineering-grade control:

| Need | HiveMind |
|---|---|
| Add an agent without a custom build | One YAML file plus a markdown job description. `hivemind new` scaffolds both. |
| Repeatable, reviewable, reversible | Specs live in git. `plan` shows the diff, `apply` creates a new immutable Foundry agent version. |
| Part shared, part private knowledge | Instructions and knowledge are layered: **org → role → team → agent**. Knowledge sources have audiences. |
| Business-owned instructions | A **HiveMind SharePoint site**: business users drop Word documents into an organisation, role, team or agent folder, and the agents follow them. Edits show up in `plan` and sync hourly. |
| Any tool, including custom MCP | A tool catalog: MCP servers (Fabric, your own), OpenAPI, AI Search, SharePoint, Fabric data agents, Work IQ and more. Copilot-style M365 capabilities come from Work IQ and its MCP servers: see [docs/m365-capabilities.md](docs/m365-capabilities.md). |
| One place to run it | `hivemind ui`: a local console with the org chart, fleet, compiled prompts, plan, tasks and a built-in CLI. Production is read-only there. |
| Agents that work as a team | A hierarchy (reports to, reviewed by, collaborates with) that drives instructions *and* enforced permissions. |
| Work that can be tracked | Delegation, escalation and review become **tasks** in a SharePoint list, with goal ancestry, parent tasks, atomic claims, retries and follow-ups. People can assign tasks to agents in the same list. |
| Outputs people can use | Agents save Word documents to an "Agent Outputs" library, filed by team and agent. |
| Real identities | Uses the Microsoft Entra agent identities Foundry provisions. The hub verifies agent tokens; no shared secrets in production. |
| Shared organisational memory | Scoped memory (`org`, `team:<x>`, custom scopes) with read/write ACLs, on Foundry memory stores. |
| Unattended and in M365 | Schedules become Foundry routines; `hivemind publish` puts an agent in Microsoft 365 Copilot and Teams. |
| Governance and cost | Policy checks in CI (owners, budgets, model allow-list, reviewers for write tools), fleet inventory, cost queries, pause/resume. |

HiveMind **builds as little as possible**. Foundry runs the agents, versions them, hosts memory and schedules,
traces every call and publishes to M365. HiveMind adds the parts Microsoft doesn't provide out of the box: the
declarative org model, the compiler, the hierarchy-aware hub and task queue, and fleet governance. See
[docs/architecture.md](docs/architecture.md), and [docs/paperclip-review.md](docs/paperclip-review.md) for the ideas
we took from the open-source Paperclip project and the ones we didn't.

## Quick start

```bash
pip install -e ".[azure,hub,dev]"

cd examples/contoso
hivemind ui                             # the admin console: overview, org chart, agents, plan, tasks + a CLI
```

Or from a terminal:

```bash

hivemind validate                       # schema, references, hierarchy, policies
hivemind tree                           # the org chart (add --format mermaid for a diagram)
hivemind render ap-clerk -e local       # the exact instructions + tools ap-clerk will get
hivemind plan -e local                  # what would change
hivemind apply -e local                 # deploy to a local file-backed provider (no Azure needed)
hivemind inventory -e local             # the fleet, with deployed versions
hivemind tasks add finance-manager "Day-2 close status for the CFO" --goal fast-close -e local
hivemind tasks run -e local             # work the queue (the hub does this continuously)
hivemind tasks list -e local
```

The local environment uses `examples/contoso/sharepoint/` as a stand-in for the SharePoint site. Look at
`Agent Rules/Agents/ap-clerk/Supplier exceptions.docx`, then `hivemind render ap-clerk -e local`.

Then deploy to Foundry for real. Infrastructure (Foundry, the hub on Container Apps, identities) is Bicep in
[infra/](infra/README.md). After that:

```bash
export FOUNDRY_ENDPOINT_DEV="https://<resource>.services.ai.azure.com/api/projects/<project>"
az login
hivemind plan  -e dev
hivemind apply -e dev
hivemind invoke finance-manager "Where are we with month-end close?" -e dev
hivemind publish chief-of-staff -e dev  # Microsoft 365 Copilot + Teams
hivemind sharepoint provision -e dev    # rules/outputs libraries + task list on the HiveMind site
```

## Adding an agent

```bash
hivemind new treasury-analyst --role worker --team finance --reports-to finance-manager \
  --owner treasury@contoso.com --description "Forecasts cash and manages bank balances."
```

Edit `agents/finance/treasury-analyst.yaml` (tools, knowledge, schedules) and
`instructions/agents/treasury-analyst.md` (its job), open a pull request, and CI validates it. Its manager's
instructions pick up the new report automatically. See [docs/authoring-agents.md](docs/authoring-agents.md).

After deployment, the business owns the detail: `sharepoint provision` creates an `Agents/treasury-analyst` rules
folder for them. See [docs/business-users.md](docs/business-users.md).

## Repository layout

```
src/hivemind/           the framework
  schema.py             the agent / role / tool / org models
  loader.py             loads an org directory, environment variables and overlays
  instructions.py       layered instruction composition (repo files, SharePoint, URLs)
  hierarchy.py          the org chart, and who may delegate, escalate and review
  access.py             shared-memory scopes and ACLs
  compiler.py           spec -> Foundry agent definition (wire JSON), content hash, metadata
  policy.py             governance checks
  planner.py            plan / apply
  providers/            foundry (azure-ai-projects) and local
  hub/                  the HiveMind hub: MCP server, task runner, Entra/key authentication
  tasks.py              the task queue (SharePoint list, file or memory)
  workspace.py          the SharePoint site: rules in, outputs out
  sharepoint.py         site provisioning
  graph.py, docx.py     minimal Microsoft Graph client; Word read/write
  reporting.py          inventory and budgets
  ui/                   the local admin console (`hivemind ui`)
examples/contoso/       a complete example organisation (with a local SharePoint stand-in)
infra/                  Bicep for Foundry, monitoring, the hub and identities; setup scripts
observability/          KQL for per-agent cost and hub activity
deploy/                 hub container image
.github/workflows/      CI (validate on every PR) and deploy (OIDC to Foundry)
docs/                   architecture, authoring guide, roadmap
```

## Commands

| Command | What it does |
|---|---|
| `hivemind validate [-e ENV] [--strict]` | Load, policy-check and compile every agent. Run in CI. |
| `hivemind tree [--format mermaid]` | Org chart. |
| `hivemind render AGENT [--json]` | Show the compiled instructions and Foundry definition. |
| `hivemind inventory [-e ENV] [--format markdown\|json]` | Fleet report; with `-e`, includes deployed versions. |
| `hivemind budget` | Declared monthly budget by team and cost centre. |
| `hivemind new AGENT --role R` | Scaffold an agent spec and instruction file. |
| `hivemind plan -e ENV [--detailed-exitcode]` | Diff specs against the environment. |
| `hivemind apply -e ENV [--prune] [--yes]` | Create agent versions, memory stores and schedules. `--prune` deletes removed agents. |
| `hivemind publish AGENT -e ENV` | Publish to Microsoft 365 Copilot and Teams. |
| `hivemind invoke AGENT MESSAGE -e ENV` | Talk to a deployed agent. |
| `hivemind pause\|resume AGENT -e ENV` | Disable or enable an agent in Foundry. |
| `hivemind identities -e ENV` | Agents' Entra principal ids, for RBAC on downstream resources. |
| `hivemind sharepoint provision -e ENV` | Create or update the rules and outputs libraries and the task list. |
| `hivemind tasks list\|show\|add\|run -e ENV` | Work with the task queue. |
| `hivemind hub serve -e ENV` | Run the hub MCP server and task runner. |
| `hivemind ui [-e ENV]` | Local admin console in the browser, with a console for these commands. |

## Status

v0.2, a working foundation. The compiler, policies, planner, local provider, hub, task queue, authentication and
SharePoint layer are covered by tests, including a SharePoint task store against an emulated Graph API. The
Foundry provider targets `azure-ai-projects` 2.8 and has been checked against the SDK's request serialisation.
The Bicep templates build and lint cleanly. Neither Foundry nor SharePoint has been exercised against a live
tenant yet. Start with [docs/administrator.md](docs/administrator.md). Several Foundry features it uses (memory, SharePoint and Fabric
tools, routines, M365 publishing) are in preview. See [docs/roadmap.md](docs/roadmap.md).
