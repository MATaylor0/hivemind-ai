# Roadmap and open questions

## Next

- **First live deployment.** Run `apply` against a dev Foundry project and confirm each tool type end to end,
  especially the preview ones (memory, SharePoint, Fabric data agent, routines, M365 publish). Capture any API
  drift in `providers/foundry.py`, the only module that talks to Foundry.
- **Hub infrastructure as code.** Bicep for Container Apps, the hub's managed identity and Entra app registration
  (the token audience), the `hivemind-hub` agentic-identity connection, `Sites.Selected` grants on the HiveMind
  site, and a Log Analytics workspace. Build and roll the hub image in the deploy workflow.
- **Evaluations as code.** A `tests:` block per agent (prompts plus expected behaviour) run through Foundry
  evaluations in CI, so behaviour regressions block a merge the same way policy errors do.
- **Plan in pull requests.** A read-only identity that runs `plan -e prod` and comments the diff on the PR.
- **Budget guard.** `hivemind costs -e prod`: run `observability/agent-usage.kql` through `azure-monitor-query`,
  compare with each agent's `budget`, alert at `alert_at_percent`, and `pause` agents over a hard limit
  (Paperclip's hard stop, using Foundry's native disable).
- **First live SharePoint run.** Provision a dev site and check list item ETag concurrency, column indexing and
  Word conversion against real documents.

## Later

- **Approval gates.** A task status of "Awaiting approval" for outputs that need human sign-off, with a Power Automate
  flow sending a Teams approval (see [paperclip-review.md](paperclip-review.md)).
- **Team templates.** `hivemind new team --from finance-pack`: copy a packaged set of roles, agents, catalog
  entries and rules folders.
- **Agent request form.** A SharePoint list where business users request a new agent (purpose, owner, tools,
  team), and a command that turns an approved request into a pull request with the scaffolded spec.
- **Per-user delegated access.** On-behalf-of tool access for chat agents, so downstream permissions follow the
  person, not the agent.
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
3. **Task volume.** A SharePoint list suits business-paced work. If agents start generating thousands of tasks an
   hour, move the `TaskStore` to Service Bus and keep the list as a read-only view.
4. **Human in the loop.** Approvals rely on MCP `require_approval` and reviewer agents. Some processes need a
   named human approver; see "Approval gates" above.
5. **Memory hygiene.** Who curates `org` memory, and how do stale facts expire? Possibly a scheduled "librarian"
   agent per scope.
