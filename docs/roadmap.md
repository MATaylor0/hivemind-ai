# Roadmap and open questions

## Next

- **First live deployment.** Run `apply` against a dev Foundry project and confirm each tool type end to end,
  especially the preview ones (memory, SharePoint, Fabric data agent, routines, M365 publish). Capture any API
  drift in `providers/foundry.py`, the only module that talks to Foundry.
- **Hub infrastructure as code.** Bicep for Container Apps, managed identity, the `hivemind-hub` custom keys
  connection, and a Log Analytics workspace. Build and roll the hub image in the deploy workflow.
- **Evaluations as code.** A `tests:` block per agent (prompts plus expected behaviour) run through Foundry
  evaluations in CI, so behaviour regressions block a merge the same way policy errors do.
- **Plan in pull requests.** A read-only identity that runs `plan -e prod` and comments the diff on the PR.
- **Cost report command.** `hivemind costs -e prod`: run `observability/agent-usage.kql` through
  `azure-monitor-query` and compare with each agent's `budget`, flagging teams over `alert_at_percent`.

## Later

- **Microsoft Entra Agent ID.** Give each agent its own identity (Foundry agent blueprints) and have the hub
  validate the agent's token instead of the shared key and header. This also enables per-agent permissions in
  downstream systems.
- **Foundry toolboxes.** Publish catalog bundles (for example "finance-read") as Foundry toolboxes, so tool
  changes roll out without re-versioning every agent.
- **Workflow agents.** Let a manager declare a fixed process (for example invoice → review → approve) that
  compiles to a Foundry workflow agent, for cases where deterministic routing beats LLM delegation.
- **Agent 365 / Control Plane integration.** Register HiveMind metadata so fleet views in the Microsoft admin
  surfaces show owner, team, tier and budget.
- **Org-chart UI.** A read-only web view (inventory, org chart, recent hub activity, cost against budget) built
  from `inventory --format json` and the KQL queries.
- **Instruction snapshots.** Record the SharePoint item version (eTag) of each fetched source in agent metadata,
  for a full audit trail of business-edited content.

## Open questions

1. **One Foundry project or many?** One project per environment is simplest. Large organisations may want one per
   business unit, for isolation and cost attribution. The design supports both (one org directory per project).
2. **Where do agent specs live?** In this repo next to the framework, or in a separate "org" repo that pins a
   HiveMind version? A separate repo is recommended once more than one team contributes.
3. **Delegation semantics.** Delegation is synchronous today: the caller waits for the answer. Long-running work
   may need asynchronous tasks (the hub queues work and the target reports back via memory or a callback).
4. **Human in the loop.** Approvals rely on MCP `require_approval` and reviewer agents. Some processes need a
   named human approver; consider an approvals integration (Teams adaptive cards or Power Automate).
5. **Memory hygiene.** Who curates `org` memory, and how do stale facts expire? Possibly a scheduled "librarian"
   agent per scope.
