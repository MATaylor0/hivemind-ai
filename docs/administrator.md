# Running HiveMind: the administrator's guide

The administrator (usually a small platform or "agent ops" team) runs the digital workforce: they design the
organisation the agents work in, add agents and processes, deploy them safely, and decide with the business when an
agent has earned more autonomy.

| Person | Owns | Works in |
|---|---|---|
| **Administrator / agent ops** | Org model, catalog, policies, environments, processes, deployments, evals | Git repo, `hivemind ui` / CLI, GitHub Actions |
| **Agent owner** (one per agent) | Its purpose, outcomes, golden cases and promotions | Pull request reviews (CODEOWNERS) |
| **Process owner** | A process end to end, including its approval steps | Pull request reviews; approves at human steps |
| **Business users** | Day-to-day rules; corrections that become test cases | SharePoint rules library, Teams, M365 Copilot |

## 1. Set up the platform (once per environment)

[infra/README.md](../infra/README.md):

1. Bootstrap script.
2. **Infrastructure** workflow: Foundry account and project, monitoring, registry, deploy identity.
3. SharePoint grant.
4. Foundry connections for the systems in your catalog.

Model deployments are declared per environment in `hivemind.yaml` and created by azd.

## 2. Design the organisation

In the org directory:

- `hivemind.yaml`: goals, teams, the constitution (`defaults`), policies (owners, budgets, models, eval gates per
  autonomy level), and environments with their `max_autonomy` ceilings. Mark prod `protected`.
- `catalog/`: every system an agent may touch, defined once, with `access`, `audience` and `user_context`.
- `roles/`: how executives, managers, reviewers and workers behave.

## 3. Add a digital worker

```text
new treasury-analyst --role worker --team finance --reports-to finance-manager --owner treasury@contoso.com
render treasury-analyst -e local
validate
```

The scaffold starts the agent in **shadow** with an empty eval dataset. Fill in its job description, give it tools,
and collect real cases. The business writes the detail in its SharePoint rules folder.

## 4. Turn a hand-off into a process

When work moves between roles, write a process: a short spec plus an Agent Framework workflow with a human step
wherever a person must approve (see [processes.md](processes.md)). Test it locally with
`hivemind process run … -e local`. HiveMind refuses a process where supervised agents act with no human step.

## 5. Deploy

Merging to `main` runs **Deploy agents**:

1. Deploy to dev: generate `azure.yaml`, then `azd deploy --all`.
2. Run the eval gates in dev.
3. Deploy to production behind the environment's reviewers.
4. Run the eval gates in production.

`hivemind sharepoint provision` keeps a rules folder for every agent. For chat agents, `hivemind publish` puts them
in Teams and M365 Copilot. Then `hivemind identities` lists the new agent identities, which need access to
downstream systems.

## 6. Promote, demote, stop

Autonomy changes are pull requests: new golden cases, the next `autonomy.level`, `approved_by` and `evidence`.
CI checks the evidence bar and the gate checks the pass rate. Production also has its own `max_autonomy` ceiling,
which the business raises deliberately. Demotion is the same pull request in reverse. In an emergency,
`hivemind pause <agent> -e prod` from a terminal or CI stops the agent immediately.

## 7. Run the loop

| Question | Where |
|---|---|
| What exists, who owns it, how autonomous is it? | Console: Overview, Agents, Autonomy; `inventory` |
| Is production what git says? | Console: Plan (prod is read-only) |
| Are agents still good? | Production trace evals (`<agent>-production`) in Foundry; `gate` |
| What did a person correct this week? | Turn it into a case: `hivemind evals add` |
| What is it costing? | `observability/agent-usage.kql`; `budget` for declared budgets |
| Why did an agent do that? | Foundry traces in Application Insights |

## The console

`hivemind ui` serves a local web console for the org directory you run it in:

- **Overview**: counts, goals with the agents serving them, policy findings, teams.
- **Org chart**: reporting lines, reviewers and collaborators.
- **Agents**: each agent's compiled prompt at its effective autonomy, its tools, instruction sources, autonomy and
  dataset, Foundry definition and metadata.
- **Autonomy**: every agent's level, environment cap, approver, eval cases against the minimum, gate threshold, and
  the tools that are gated or held back.
- **Processes**: steps (agents and people), owner, goal, triggers and findings; run one locally.
- **Plan**: what `apply` would change.
- **Console**: `hivemind` sub-commands only, never a shell. Commands that change something ask first, and protected
  environments are read-only.

It listens on 127.0.0.1, behind a per-session token printed at start-up, and uses your own Azure sign-in.
