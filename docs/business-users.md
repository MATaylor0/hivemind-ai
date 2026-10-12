# HiveMind for business teams

You don't need to write code to shape or work with HiveMind agents.

## Changing what an agent does

Open the **Agent Rules** library on the HiveMind SharePoint site and pick a folder:

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

**When do changes take effect?** At the next sync, hourly by default, and only **approved** versions if the library
has content approval turned on (recommended). Every change creates a new, recorded version of the agent, and it must
still pass the agent's eval gate before it reaches production.

## Correcting an agent

When an agent gets something wrong, tell its owner what the right answer was. The correction becomes a **golden
test case**, and future versions are checked against it before they go live. Over time these cases are the
evidence that lets an agent take on more on its own.

## How much an agent does on its own

Every agent has an autonomy level, shown in the admin console and in its instructions:

| Level | What it means for you |
|---|---|
| Shadow | The agent works alongside you and nothing it produces is used. Compare its work with yours. |
| Suggest | It recommends; you decide and act. |
| Approve | It acts, but asks you to approve each change first. |
| Autonomous | It acts on its own within its tools; someone owns its outcomes. |
| Autonomous (sampled) | As autonomous, and some of its work is reviewed afterwards. |

An agent moves up a level only with evidence (test cases, pass rates) and a named approver from the business.

## Approving work in a process

Some work runs as a **process**: for example, invoice to pay. Agents do the steps, and the process stops at the
points where a person must approve. You'll be asked a clear question such as "Approve payment of these invoices?"
Reply `approved`, or say what must change.

## Talking to agents directly

Agents published to Microsoft 365 appear in **Microsoft 365 Copilot** and **Teams**. Search for the agent by name in
Copilot's agent list.

## Who to ask

Each agent has an accountable **owner** (shown in the fleet inventory), and each process has a process owner. For new
agents, new tools or anything that isn't working, contact the HiveMind platform team.
