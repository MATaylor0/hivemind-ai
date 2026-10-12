# Roadmap and open questions

## Next: prove it on one process

The aim is one real process (invoice to pay is the candidate) running in a dev tenant, with agents earning
autonomy on evidence, before adding anything else.

- **AP pilot.** Run `invoice-to-pay` with the AP team: ap-clerk and compliance-reviewer at `approve`, real
  invoices, a named process owner. Collect corrections as golden cases (`hivemind evals add`) and measure the
  pass rate, approval turnaround and rework against the human baseline.
- **First live azd deployment.** Run `hivemind apply -e dev` (`azd deploy --all`) against a dev Foundry project and
  confirm each piece of the generated `azure.yaml` end to end: prompt agents with every tool kind, routines, eval
  configurations, model deployments and the process host image. The azd Foundry extensions are beta, so expect to
  adjust `azd.py`; the vendored schemas in `tests/fixtures/azd-schemas/` should be refreshed with each extension
  release.
- **Process host identity.** Confirm the hosted process agent's identity can invoke the prompt agents it calls, and
  document the exact role assignment (or add it to `infra/`).
- **Teams approvals.** Decide how a human step reaches the approver: the process host invoked from Teams (published
  hosted agent), or a Power Automate approval that answers the paused session. Test the round trip with the AP team.
- **Eval gates in CI for real.** Run `hivemind gate -e dev` against Foundry evaluations, tune the judge model and
  thresholds per level, and check production trace evals (`<agent>-production`) on a schedule.

## Later

- **Durable process state.** The process host keeps paused sessions in memory with a time-to-live. For approvals
  that wait days, persist checkpoints (Agent Framework checkpoint storage on Cosmos DB or Blob) so a restart or
  scale-out doesn't lose them.
- **Budget guard.** `hivemind costs -e prod`: run `observability/agent-usage.kql`, compare with each agent's
  `budget`, alert at `alert_at_percent`, and `pause` agents over a hard limit.
- **Sampling queue.** For `autonomous_sampled` agents, route a share of completed work (`sample_rate`) to a person
  for review, and feed their verdicts back as cases.
- **Digital worker publishing.** Publish process hosts (and selected agents) as Microsoft 365 digital workers with
  their own identity, so a replaced role appears in M365 as a worker.
- **Agent 365 / Control Plane integration.** Show HiveMind metadata (owner, team, autonomy, budget, git SHA) in
  Microsoft's fleet views.
- **Foundry toolboxes and skills.** Publish catalog bundles (for example "finance-read") as toolboxes so tool
  changes roll out without re-versioning every agent.
- **Team templates.** `hivemind new team --from finance-pack`: copy a packaged set of roles, agents, processes,
  catalog entries, eval datasets and rules folders.
- **Plan in pull requests.** A read-only identity that runs `plan -e prod` and comments the diff on the PR.
- **Hosted console.** Run `hivemind ui` on Container Apps behind Entra sign-in for the platform team, with an audit
  log of console commands and cost-against-budget views.
- **Instruction snapshots.** Record the SharePoint item version of each fetched rules document in agent metadata,
  for a full audit trail of business-edited content.

## Open questions

1. **Where do specs live?** In this repo next to the framework, or in a separate "org" repo that pins a HiveMind
   version? A separate repo is recommended once more than one team contributes.
2. **Evidence standards.** How many cases, how recent, and judged how, before a business approver may promote an
   agent to `autonomous`? The defaults (30 cases, 95% pass rate) are a starting point, not a standard.
3. **Accountability.** When an autonomous agent gets it wrong, who answers for it: the agent owner, the process
   owner or the approver who promoted it? This needs a decision from the business and risk, not from the platform.
4. **Unit economics.** Cost per completed process instance (model tokens, tools, human review time) against the
   cost of the work done by people today. The pilot should measure this before the business commits to scale.
5. **One Foundry project or many?** One project per environment is simplest. Large organisations may want one per
   business unit for isolation and cost attribution; the design supports both (one org directory per project).
