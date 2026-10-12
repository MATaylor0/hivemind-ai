# HiveMind AI

**The operating model for an AI-native workforce on Microsoft Foundry.** Define your digital workers (their jobs,
place in the organisation, tools, knowledge and how far each may act on its own) as code. Run end-to-end processes
with people at the approval points. Promote agents from shadow to autonomous on measured evidence. HiveMind compiles
it all into what Microsoft's platform runs: Foundry prompt agents, Agent Framework workflows, routines and
evaluations, deployed with the Azure Developer CLI.

```
chief-of-staff [executive]  ─┬─ compliance-reviewer [reviewer]
                             ├─ finance-manager [manager] ─┬─ ap-clerk [worker]      approve · 10 golden cases
                             │                             └─ fpa-analyst [worker]   approve · 10 golden cases
                             └─ policy-assistant [worker]                             shadow (pilot)

process invoice-to-pay:  ap-clerk → compliance-reviewer → person approves → ap-clerk releases payment
```

## What it does

| Need | HiveMind | Runs on |
|---|---|---|
| Add digital workers without custom builds | One YAML spec and a job description per agent; `hivemind new` scaffolds both | Foundry prompt agents |
| Shared vs private knowledge | Layered instructions (org → role → team → agent), a tool and knowledge catalog with audiences | Foundry tools: MCP, AI Search, SharePoint, Fabric, Work IQ… |
| Business-owned rules | Word documents in per-agent SharePoint folders, compiled into instructions and versioned | SharePoint |
| Replace work safely | **Graduated autonomy**: shadow → suggest → approve → autonomous → sampled, enforced on the tools, capped per environment | MCP `require_approval` |
| Loop engineering | **Golden datasets + eval gates** per autonomy level; production trace evaluation; corrections become test cases | Foundry evaluations (`azd ai eval`) |
| Multi-agent work | **Processes**: Agent Framework declarative workflows with human steps, validated against the org | Foundry hosted agents |
| Governed, repeatable deployment | Policy-as-code in CI; `azure.yaml` generated for azd; dev → gate → prod | azd, GitHub OIDC |
| One place to run it | `hivemind ui`: org chart, agents with compiled prompts, autonomy and evidence, processes, plan, and a built-in CLI (prod read-only) | local |

What HiveMind doesn't do: run agents, host a service, or replace Microsoft's registry and monitoring (Foundry
Control Plane, Agent 365). It feeds them: every agent carries owner, team, autonomy, budget and git SHA metadata.

## Quick start

```bash
pip install -e ".[azure,processes,dev]"
cd examples/contoso

hivemind ui                                   # the admin console
# or, from a terminal:
hivemind validate                             # org, policies, autonomy evidence, processes
hivemind render ap-clerk -e local             # the exact prompt and tools ap-clerk gets at its autonomy level
hivemind apply -e local --yes                 # deploy to the local stand-in provider
hivemind gate -e local                        # eval gates (local smoke run)
hivemind process run invoice-to-pay "Process today's invoices" -e local --answer approved
hivemind build -e dev --offline               # the azd project for dev: .hivemind/build/dev/azure.yaml
```

Deploying for real: provision the platform with [infra/](infra/README.md), then the **Deploy agents** workflow runs
`hivemind apply -e dev` (that is, `azd deploy --all`), `hivemind gate -e dev`, and promotes to prod after approval.

## Commands

| Command | What it does |
|---|---|
| `validate [-e ENV] [--strict]` | Schema, references, hierarchy, policies, autonomy evidence, process checks |
| `tree [--format mermaid]` · `inventory` · `budget` | Org chart, fleet table, declared budgets |
| `render AGENT [-e ENV] [--json]` | Compiled instructions and Foundry definition at the agent's effective autonomy |
| `new AGENT --role R` | Scaffold an agent (starts in shadow) with its instructions and an eval dataset |
| `build -e ENV [--offline]` | Generate the azd project (`azure.yaml`, datasets, process hosts) |
| `plan -e ENV` · `apply -e ENV` | Diff against the deployed fleet; deploy (azd, or the local/SDK provider) |
| `gate -e ENV [--dry-run]` | Run each agent's eval gate at its level's pass rate |
| `evals add AGENT --query … --expected …` | Add a golden case (for example, a human correction) |
| `process list` · `process run NAME INPUT -e local` | Processes and their steps; run one locally |
| `publish` · `invoke` · `pause` · `resume` · `identities` | M365 publishing, test calls, emergency stop, Entra identities |
| `sharepoint provision -e ENV` | Rules library with a folder per org, role, team and agent |
| `ui` | Local admin console |

## Repository layout

```
src/hivemind/
  schema.py, loader.py        the org model (agents, roles, catalog, processes, environments)
  instructions.py, workspace.py, docx.py, graph.py   layered instructions; SharePoint Word rules
  autonomy.py                 levels, environment caps, tool gating, operating-mode instructions
  evals.py                    golden datasets, gates, eval service config
  processes.py                workflow validation, renaming, local runner
  compiler.py                 spec -> Foundry prompt agent definition
  azd.py                      org -> azure.yaml (agents, routines, evals, process hosts)
  runtime/process_host.py     generic Agent Framework host deployed per process
  policy.py, planner.py, providers/, reporting.py, sharepoint.py, ui/, cli.py
examples/contoso/             a complete example org (agents, processes, eval datasets, local SharePoint stand-in)
infra/                        Bicep: Foundry account and project, monitoring, registry, deploy identity
tests/                        including azure.yaml validation against azd's published schemas
docs/                         architecture, autonomy and evals, processes, administrator, business users, authoring
```

## Docs

- [Architecture](docs/architecture.md): what HiveMind owns and what Microsoft runs
- [Autonomy and evals](docs/autonomy-and-evals.md): how a digital worker earns independence
- [Processes](docs/processes.md): end-to-end work with people at the approval points
- [Administrator guide](docs/administrator.md) · [Business users](docs/business-users.md) · [Authoring agents](docs/authoring-agents.md)
- [M365 capabilities](docs/m365-capabilities.md) · [Paperclip review](docs/paperclip-review.md) · [Roadmap](docs/roadmap.md)

## Status

v0.3: the hub and task queue from v0.2 are retired. Orchestration moved to Agent Framework and deployment to azd,
both from Microsoft. Covered by tests:

- the compiler, autonomy and policy rules
- evals and the azure.yaml generation, validated against azd's published schemas
- process validation, and local runs on Agent Framework itself
- the generated process host, over HTTP
- the console

Not yet run against a live tenant: `azd deploy` of the generated project, Foundry evaluations and hosted process
agents. Several of these Foundry and azd features are in preview (the azd Foundry extensions are beta). See the
[roadmap](docs/roadmap.md).
