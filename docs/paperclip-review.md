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
| **Goal ancestry** | Every task links up to company goals, so agents know the "why" | `goals:` in `hivemind.yaml` (with parents). Agents and processes list the goals they serve, compiled into instructions. |
| **Org chart with roles** | Agents placed in a hierarchy with managers | `roles/`, `reports_to`, reviewers and collaborators, compiled into each agent's position and escalation instructions. |
| **Governance before autonomy** | Board approval for hires and strategy | **Graduated autonomy** (shadow → suggest → approve → autonomous → sampled) promoted by pull request with a named approver and eval evidence, capped per environment. |
| **Runaway protection** | Budgets and hard stops | Declared budgets, environment autonomy ceilings, and `pause`/`resume` mapped to Foundry's native enable/disable. |
| **Wake on schedule** (heartbeats) | DB-backed wake-up queue | Foundry **routines** for agents (`schedules:`) and processes (`triggers:`). |

## Superseded in v0.3

v0.2 also adopted Paperclip's task model: a SharePoint "Agent Tasks" list with atomic checkout, a hub service that
woke agents on assigned work, delegation between agents, follow-up messages, and an "Agent Outputs" library. v0.3
retired all of it. Multi-agent work now runs as **processes** (Agent Framework declarative workflows hosted in
Foundry, with human steps where a person approves), so Microsoft owns the runtime, state and retries. Outputs land
where the process puts them (the systems of record, SharePoint or OneDrive through tools), not in a HiveMind library.

## Later (good ideas, not yet worth the code)

- **Budget hard stops.** Paperclip pauses an agent when its spend crosses a limit. We have declared budgets, a
  cost query and now `pause`. The missing piece is a scheduled job that runs the cost query and pauses agents
  over `budget.monthly_usd`. Small, once the KQL has been checked against real traces.
- **Team templates.** Paperclip installs ready-made teams (roles, skills, routines). We could package an org
  sub-directory (a role, agents, catalog entries, rules folders) as a template that `hivemind new team` copies.
- **Agents assigning work to people.** Beyond approval steps in processes, an agent handing a person a piece of
  work (shown in their Teams activity feed). This needs care around notifications.

## Not adopting

- **Autonomous "AI company" framing**, such as agents hiring agents or self-set goals. HiveMind agents are
  defined and approved by people through git or SharePoint. Hire approvals are a pull request.
- **Coding-agent workspaces** (git worktrees, dev servers, preview URLs). These are out of scope for business
  agents.
- **Plugin system.** The catalog plus raw Foundry tool JSON covers extension without a plugin runtime.

Sources: [paperclipai/paperclip on GitHub](https://github.com/paperclipai/paperclip),
[Abduzeedo overview](https://abduzeedo.com/paperclip-open-source-ai-agent-orchestration-builders),
[ZenML LLMOps database entry](https://www.zenml.io/llmops-database/open-source-agent-orchestration-platform-for-multi-agent-business-automation).
