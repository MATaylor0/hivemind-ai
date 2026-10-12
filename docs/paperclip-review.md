# What we took from Paperclip (and what we didn't)

[Paperclip](https://github.com/paperclipai/paperclip) is an open-source (MIT) platform for running "companies"
of AI agents. A Node.js server and React UI organise agents (Claude Code, Codex, Cursor, HTTP bots and others)
into an org chart with goals, tasks, budgets and governance. It solves the same problem as HiveMind for a
different world: it brings its own runtime, UI, secrets store and scheduler, where HiveMind runs on
Microsoft Foundry and Microsoft 365.

We read it for ideas, filtered by one rule: **adopt an idea only if Microsoft doesn't already provide it, and
put it where our users already work (SharePoint, Teams, Foundry)**.

## Adopted

| Paperclip idea | How it works there | HiveMind version |
|---|---|---|
| **Tasks as the unit of work** | Issues with one assignee, threads, attachments, run history | Delegation, escalation and review create tasks (`hivemind.tasks`). The queue is a **SharePoint list** ("Agent Tasks"), so people see, create and follow work in SharePoint, Teams or the Lists app. No new UI. |
| **Goal ancestry** | Every task links up to company goals, so agents know the "why" | `goals:` in `hivemind.yaml` (with parents). Agents list the goals they serve (compiled into instructions), and tasks carry a goal that is passed down to sub-tasks and shown in each task message. |
| **Atomic checkout** | Single assignee plus execution locks | Claims are compare-and-set on the list item's ETag, so several hub replicas never work the same task twice. Leases return abandoned tasks to the queue, with a retry limit. |
| **Wake on assigned work** (heartbeats) | DB-backed wake-up queue | The hub's task runner polls the queue and invokes the assigned Foundry agent. Timer-based wake-ups are Foundry **routines** (already in `schedules:`). |
| **Follow-up messages wake the requester** | Comments and follow-ups wake the agent | When a delegated task finishes and nobody waited for it, the result goes back to the requester as a follow-up task linked to the original parent task. Delivery is exactly once. |
| **Runaway protection** | Budgets and hard stops | Durable delegation depth (stored on each task) plus a cap on tasks spawned per original request (`max_tasks_per_request`). |
| **Artifacts in one place** | Work products collected and previewable | `save_output` files Word documents in the SharePoint "Agent Outputs" library by team and agent, and links them to the task. |
| **Pause / resume agents** | Pause, resume, terminate | `hivemind pause|resume`, which maps to Foundry's native agent enable/disable. |
| **People and agents in one hierarchy** | Humans and agents share the org chart | Partly: people assign work to agents through the task list, and owners are recorded per agent. Agents don't assign tasks to people yet; see "Later". |

## Adapted to Microsoft instead of rebuilt

| Paperclip feature | Why not build it | Microsoft equivalent |
|---|---|---|
| Web UI / dashboard | Non-technical users already live in M365 | SharePoint site (rules, outputs, task list), Teams, M365 Copilot |
| Adapters for many agent runtimes | We are Foundry-first | Foundry Agent Service. External agents can join via Foundry's A2A tool (`kind: a2a` in the catalog). |
| Secrets store | Credentials shouldn't live in our system | Foundry project connections backed by Key Vault |
| Scheduled routines | Already native | Foundry routines (`schedules:`) |
| Skills / Skill Studio | Shared instructions | Layered instructions plus SharePoint rules folders. Foundry **skills** and **toolboxes** are on the roadmap for reusable capabilities. |
| Audit log | Already native | Foundry tracing to Application Insights, the hub's JSON audit log, and SharePoint version history on rules and tasks |
| Revisioned config with rollback | Already native | Git history plus immutable Foundry agent versions |
| Connections with Allowed / Ask first / Off | Already native | MCP `require_approval` per tool, and tool audiences |
| Multi-company tenancy | Not our problem | One org directory per Foundry project |

## Later (good ideas, not yet worth the code)

- **Budget hard stops.** Paperclip pauses an agent when its spend crosses a limit. We have declared budgets, a
  cost query and now `pause`. The missing piece is a scheduled job that runs the cost query and pauses agents
  over `budget.monthly_usd`. Small, once the KQL has been checked against real traces.
- **Approval gates on task completion.** For outputs that need a human sign-off: a task status of
  "Awaiting approval", with a Power Automate flow on the task list sending a Teams approval. This is low-code on
  the Microsoft side rather than our code.
- **Team templates.** Paperclip installs ready-made teams (roles, skills, routines). We could package an org
  sub-directory (a role, agents, catalog entries, rules folders) as a template that `hivemind new team` copies.
- **Agents assigning work to people.** An agent creates a task with a person as assignee (shown in their Teams
  activity feed). This needs care around notifications.

## Not adopting

- **Autonomous "AI company" framing**, such as agents hiring agents or self-set goals. HiveMind agents are
  defined and approved by people through git or SharePoint. Hire approvals are a pull request.
- **Coding-agent workspaces** (git worktrees, dev servers, preview URLs). These are out of scope for business
  agents.
- **Plugin system.** The catalog plus raw Foundry tool JSON covers extension without a plugin runtime.

Sources: [paperclipai/paperclip on GitHub](https://github.com/paperclipai/paperclip),
[Abduzeedo overview](https://abduzeedo.com/paperclip-open-source-ai-agent-orchestration-builders),
[ZenML LLMOps database entry](https://www.zenml.io/llmops-database/open-source-agent-orchestration-platform-for-multi-agent-business-automation).
