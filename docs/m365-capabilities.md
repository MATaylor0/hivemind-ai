# Matching Microsoft 365 Copilot with Foundry agents

**Mostly yes**, through catalog entries rather than custom builds. Microsoft exposes the same work data Copilot uses
to Foundry agents ("Work IQ"), plus MCP servers for acting in Outlook, Teams, SharePoint and OneDrive. A few things
differ, and they matter for design.

## Capability map

| What Copilot does | Foundry building block | HiveMind catalog `kind` | Works unattended? |
|---|---|---|---|
| Answer from my mail, meetings, chats and files | **Work IQ** tool (Foundry; also via a toolbox or A2A) | `work_iq` | No: on behalf of the signed-in user |
| Send or reply to mail, manage calendar, post in Teams, work with OneDrive and SharePoint files | **Work IQ MCP servers** (Agent 365 tooling servers: Mail, Calendar, Teams, SharePoint/OneDrive…; preview) | `mcp` with `user_context: true` | No: acts as the user |
| Search SharePoint as the user (permission-trimmed) | **SharePoint** tool | `sharepoint` (knowledge) | No: user identity |
| Search a document library for everyone | **Azure AI Search** index of the library | `azure_ai_search` | Yes |
| Analyse data | **Fabric data agent**, **Fabric IQ**, Fabric MCP, code interpreter | `fabric_data_agent`, `fabric_iq`, `mcp`, `code_interpreter` | Yes (agent identity) |
| Create Word, Excel and PowerPoint files | Code interpreter (generates .docx/.xlsx/.pptx); OneDrive/SharePoint MCP servers (save files where people work) | `code_interpreter`, `mcp` | Yes |
| Web answers | Web search, Bing grounding | `web_search`, `bing_grounding` | Yes |
| Live in Teams and M365 Copilot | **Publish to Microsoft 365** | `channels.m365` + `hivemind publish` | n/a |

## The differences that matter

1. **User context versus agent context.** Copilot always acts as a person. A HiveMind agent may be chatting with a
   person (Teams/Copilot) or running unattended (schedules, process steps). Work IQ, the Work IQ MCP
   servers and the SharePoint tool act as the signed-in person, so they do nothing in unattended runs.
   - Mark such tools `user_context: true` in the catalog (SharePoint and Work IQ knowledge default to it).
     `hivemind validate` warns when an agent has them but is scheduled, or isn't published to M365.
   - For unattended agents, use agent-identity access instead: index SharePoint libraries into Azure AI Search, use
     Fabric with the agent's identity, or use your own MCP servers with app permissions.
   - The Contoso example shows both patterns: `org-policies` (AI Search, everyone) and `policy-library`
     (SharePoint, as the employee asking, for the Policy Assistant).
2. **Licensing.** Users whose requests use Work IQ need a Microsoft 365 Copilot licence. Check the current Agent 365
   and Copilot licensing for published agents used by unlicensed staff.
3. **Inside the Office apps.** Copilot in Word or Excel edits the document you have open. A Foundry agent works
   with *files* (reads them, creates new ones, saves them to SharePoint or OneDrive) and is reached through Teams or
   M365 Copilot Chat once published. It doesn't drive the Word or Excel canvas. For "edit this open document"
   scenarios, Copilot itself is the right tool, and HiveMind agents can sit alongside it in the same chat.
4. **Preview status.** Work IQ for Foundry and the Work IQ MCP servers are in preview, and Work IQ doesn't support
   VNet-restricted Foundry projects. Expect names and setup to change.
5. **Approvals.** Acting as a user (sending mail, posting messages) should be explicit. Catalog entries for those
   tools use `require_approval: always` so the person confirms each action in chat. HiveMind's policy enforces an
   approval or a reviewer for any `access: write` tool.

## Digital workers in Microsoft 365

Microsoft is building the "agent that does a job" pattern into M365 directly. azd can publish an Activity-protocol
hosted agent as a **Microsoft 365 Digital Worker**, with tenant scope and optional Work IQ permission scopes (Mail,
Calendar…). HiveMind's prompt agents publish to Teams and M365 Copilot with `hivemind publish`. Digital-worker
publishing of hosted agents is on the roadmap, so a "replaced role" can appear in M365 as a worker with its own
identity.

## Adding one

```yaml
# catalog/tools/work-iq-calendar.yaml
id: work-iq-calendar
kind: mcp
description: Find times and create meetings as the signed-in user.
server_url: ${var:WORK_IQ_CALENDAR_URL}   # from the Agent 365 tooling catalog
connection: work-iq-calendar              # Foundry project connection (OAuth, as the user)
access: write
require_approval: always
user_context: true
audience: ["tier:executive", "tier:manager"]
```

Then add `work-iq-calendar` to the `tools:` of the agents (or roles) that should have it, and open a pull request.

Sources: [Work IQ tool for Foundry agents](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/work-iq),
[Work IQ MCP / Agent 365 tooling servers](https://learn.microsoft.com/microsoft-agent-365/tooling-servers-overview),
[Agent 365 MCP servers connector](https://learn.microsoft.com/en-us/connectors/a365mcpservers/).
