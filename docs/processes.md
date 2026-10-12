# Processes

A process is work that spans agents and people, end to end: "invoice to pay", "new starter onboarding", "month-end
close". It is how an AI-native organisation replaces a hand-off between roles with something explicit, testable
and approvable.

Each process is two files in `processes/`:

```yaml
# processes/invoice-to-pay.yaml - the HiveMind spec
apiVersion: hivemind/v1
kind: Process
name: invoice-to-pay
description: Codes the day's supplier invoices, has them independently reviewed, and releases payment once a person approves.
owner: financial.controller@contoso.com
goal: working-capital
workflow: processes/invoice-to-pay.workflow.yaml
triggers:
  - name: weekday-run
    cron: "0 10 * * 1-5"
    time_zone: AUS Eastern Standard Time
    input: Process all supplier invoices received since the last run.
budget:
  monthly_usd: 400
  cost_center: FIN-210
```

```yaml
# processes/invoice-to-pay.workflow.yaml - a Microsoft Agent Framework declarative workflow
kind: Workflow
trigger:
  kind: OnConversationStart
  id: invoice_to_pay
  actions:
    - kind: InvokeAzureAgent
      id: code_invoices
      agent: {name: ap-clerk}
    - kind: InvokeAzureAgent
      id: compliance_review
      agent: {name: compliance-reviewer}
    - kind: RequestExternalInput
      id: payment_approval
      prompt: Review the coding and the compliance review above. Reply 'approved' to release the payment run.
      variable: Local.approval
    - kind: InvokeAzureAgent
      id: release_payments
      agent: {name: ap-clerk}
```

The workflow is Microsoft's format, so everything Agent Framework supports is available: branching (`If`,
`ConditionGroup`), loops (`Foreach`), Power Fx expressions (`=Local.result`), HTTP and MCP tool calls, and questions
to people. Agents are referred to by their HiveMind names; HiveMind renames them per environment.

## What HiveMind checks

- Every `InvokeAzureAgent` names a defined, active agent.
- If any agent below `autonomous` takes part, the workflow contains a human step (`RequestExternalInput` or
  `Question`). Supervised agents can't run a process end to end on their own.
- Agents in `shadow` mode in a live process produce a warning.
- The process has an owner, and its goal exists.

## Running it

| Where | How |
|---|---|
| Locally | `hivemind process run invoice-to-pay "Process today's invoices" -e local --answer approved` (or answer each human step interactively). Agents are the local provider's stand-ins. |
| Foundry | `hivemind apply` deploys it as a hosted agent named `process-invoice-to-pay` (with the environment prefix), and its triggers as routines. |
| Approvals | The host returns `{"status": "awaiting_input", "session", "requests": [...]}`; resume with `{"session", "responses": {"<request id>": "approved"}}` on the same agent. A Teams approval card via Power Automate is on the roadmap. |

The host image includes the .NET runtime, because Agent Framework evaluates Power Fx expressions on it. Paused runs
are held for the hosted agent session (default TTL 24 hours via `HIVEMIND_SESSION_TTL_SECONDS`). Durable checkpoints
are on the roadmap.
