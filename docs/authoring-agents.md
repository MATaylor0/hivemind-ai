# Authoring agents

This guide is for anyone adding or changing an agent. You need a text editor and a pull request. You don't need
Python.

## 1. Scaffold

```bash
hivemind new <agent-name> --role worker --team finance --reports-to finance-manager --owner you@contoso.com
```

This creates `agents/<team>/<agent-name>.yaml` and `instructions/agents/<agent-name>.md`.

## 2. Write the job description

`instructions/agents/<agent-name>.md` is the agent's job. Write it like a brief for a capable new starter:

* the outcomes it owns, and what "done" looks like
* how it should work, step by step where order matters
* hard limits: what it must never do, and when it must stop and ask

Don't repeat what other layers already provide:

| Already provided by | Content |
|---|---|
| `defaults.instructions` (org) | values, safety, tone, language |
| the role | how a worker, manager or reviewer behaves |
| the team | domain facts, fiscal calendar, delegations of authority |
| generated | who it reports to, its reports and colleagues, its tools and memory scopes |

Run `hivemind render <agent-name> -e local` to read the final prompt exactly as the agent will see it.

### Instructions owned by the business, in SharePoint

When a business team wants to change part of an agent's guidance without a pull request (an exceptions list, a
price list, a FAQ), add a SharePoint source:

```yaml
instructions:
  - file: instructions/agents/ap-clerk.md
  - sharepoint: {site: contoso.sharepoint.com/sites/Finance, path: "Agents/ap-clerk-exceptions.md"}
    title: Current exceptions
```

The document is fetched on every `plan` and `apply`. If it changed, the agent shows as changed and gets a new
version. The deploy identity needs read access to the site (`Sites.Selected` is recommended).

### Business rules on the HiveMind SharePoint site

Every agent also gets a folder in the site's **Agent Rules** library (`Agents/<agent-name>`), plus shared folders
for its team, role and the whole organisation. Business owners maintain the detail there in Word, without pull
requests. Keep the repo instructions for the stable core of the job, and leave fast-changing specifics (exceptions,
calendars, price lists) to the business. See [business-users.md](business-users.md).

## 3. Give it tools and knowledge

```yaml
tools: [erp-invoices, fabric-finance-warehouse]   # ids from catalog/tools
knowledge: [finance-handbook]                      # ids from catalog/knowledge
```

Your agent also inherits the org defaults, its role's tools and its team's knowledge. If `validate` says
`not in the audience of …`, the tool's owner hasn't approved your agent, team or role. Ask them to add it to
the tool's `audience`.

New system? Add one catalog file, for example `catalog/tools/servicenow.yaml`:

```yaml
id: servicenow
kind: mcp
description: Create and update ServiceNow incidents.
server_url: ${var:SERVICENOW_MCP_URL}
connection: servicenow-mcp          # Foundry project connection holding credentials
access: write
audience: ["team:it"]
require_approval:
  always: [close_incident]
```

Supported kinds: `mcp`, `openapi`, `azure_ai_search`, `sharepoint`, `fabric_data_agent`, `fabric_iq`, `work_iq`,
`code_interpreter`, `web_search`, `bing_grounding`, `a2a`, and `raw` (any Foundry tool JSON, as an escape hatch).

## 4. Place it in the organisation

```yaml
reports_to: finance-manager      # who it escalates to, and who can delegate to it
reviewed_by: compliance-reviewer # required if it has write tools without approval gates
collaborates_with: [fpa-analyst] # peers it can hand work to (both directions)
```

## 5. Memory

```yaml
memory:
  private: true          # its own long-term memory (Foundry memory store)
  private_scope: user    # 'user' (default): per end user. 'agent': one memory, for unattended agents
  shared: [month-end-close]   # opt in to custom shared scopes (team and org scopes are automatic)
```

## 6. Run it unattended, or put it in Teams

```yaml
schedules:
  - name: morning-sweep
    cron: "30 7 * * 1-5"
    time_zone: AUS Eastern Standard Time
    input: Process all invoices received since the last sweep.

channels:
  m365:
    enabled: true
    scope: shared        # personal | shared | tenant (tenant usually needs M365 admin approval)
```

After `apply`, run `hivemind publish <agent> -e prod` to publish to Microsoft 365 Copilot and Teams.

## 7. Goals

```yaml
goals: [working-capital]   # ids from `goals:` in hivemind.yaml
```

The agent's instructions list the goals it serves (with their parent goals), and tasks it delegates carry the goal
down the chain.

## 8. Ownership and budget

```yaml
owner: ap.lead@contoso.com
budget:
  monthly_usd: 300
  cost_center: FIN-210
tags:
  process: procure-to-pay
```

## 9. Ship

```bash
hivemind validate
hivemind plan -e dev
```

Open a pull request. CI validates it and posts the org chart. After merge, the deploy workflow plans and
applies.
