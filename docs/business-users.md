# HiveMind for business teams

You don't need to write code to shape or work with HiveMind agents. Everything happens on the **HiveMind
SharePoint site**.

## The site

| Where | What it's for |
|---|---|
| **Agent Rules** (document library) | Rules and guidance the agents follow. Drop a Word document in the right folder. |
| **Agent Outputs** (document library) | Reports, briefings and analyses the agents produce, filed by team and agent. |
| **Agent Tasks** (list) | Work for agents: what was asked, who is doing it, status and results. Add tasks yourself. |

Add the site to Teams as a tab, or open the Agent Tasks list in the Microsoft Lists app.

## Changing what an agent does

Open **Agent Rules**. Pick a folder:

| Folder | Affects |
|---|---|
| `Organisation` | every agent |
| `Teams/<team>` | every agent in that team |
| `Roles/<role>` | every agent with that role (for example, all managers) |
| `Agents/<agent>` | one agent |

Add or edit a Word document in that folder. Each folder has a `_How to use this folder` guide.

Tips for writing rules:

- One topic per document, with a clear name ("Supplier exceptions", "Month-end calendar").
- Say what the agent must **always** do, must **never** do, and how to handle **exceptions**.
- Use headings and bullet points. Agents read structure well.
- To control the order, start file names with `01-`, `02-`…
- Files starting with `_` are ignored, so use that for notes to yourselves.

**When do changes take effect?** At the next sync, hourly by default. If the library has content approval
turned on (recommended), only **approved** versions are used. Every change creates a new, recorded version of
the agent, and the platform team can see exactly which documents went into it.

## Giving an agent a task

In **Agent Tasks**, select **New** and fill in:

- **Title**: a short name for the task
- **AssignedAgent**: pick the agent from the drop-down
- **Instructions**: what you need, with any context the agent will need (it can't see your email)
- **Goal** (optional): the organisational goal this supports

Status moves from **Queued** to **InProgress** to **Done** (or **Failed**, with the reason in ErrorMessage).
The answer appears in **Result**, and longer documents appear as a link in **OutputLink**. If the agent hands
part of the work to colleagues, you'll see those tasks too, linked by **ParentTask** and **RootTask**.

To be notified when your task is done, use the list's **Alert me** or a Power Automate flow ("When an item is
modified" with Status = Done, then post in Teams).

## Talking to agents directly

Agents published to Microsoft 365 appear in **Microsoft 365 Copilot** and **Teams**. Search for the agent by
name in Copilot's agent list. Use tasks for work that can run in the background, and chat for quick questions.

## Who to ask

Each agent has an accountable **owner** (shown in the fleet inventory). For new agents, new tools or anything
that isn't working, contact the HiveMind platform team.
