"""Provision the HiveMind SharePoint site so non-technical people can shape and follow the agents.

Creates (idempotently):

* **Agent Rules** library - a folder for the organisation, each role, team and agent, each with a short
  "_How to use this folder" Word document. Documents dropped in a folder become part of those agents' rules.
* **Agent Outputs** library - a folder per team and agent for the documents agents save.
* **Agent Tasks** list - the task queue. People can add a task for an agent (pick it from the AssignedAgent
  drop-down), and follow status and results.

Run it after adding agents so the folders and the agent drop-down stay current.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .docx import markdown_to_docx
from .graph import GraphClient, GraphError
from .loader import Org
from .tasks import COLUMNS

GUIDE_NAME = "_How to use this folder.docx"  # leading underscore: never read as a rule


def rule_folder_guides(org: Org) -> dict[str, str]:
    """Rules folder path -> guide text."""
    common = (
        "Put Word documents (or .txt/.md files) in this folder. At the next deployment their text is added to "
        "the instructions of {who}. Files are read in name order, so prefix names with 01-, 02- to control order. "
        "Write plainly: what the agent must always do, must never do, and how to handle exceptions. Keep each "
        "document focused on one topic. Changes appear in the deployment plan for review before they go live."
    )
    guides = {"Organisation": common.format(who="every agent in the organisation")}
    for role_id in sorted(org.roles):
        guides[f"Roles/{role_id}"] = common.format(who=f"every agent with the '{role_id}' role")
    for team in sorted(org.config.teams):
        guides[f"Teams/{team}"] = common.format(who=f"every agent in the {team} team")
    for name, agent in sorted(org.agents.items()):
        guides[f"Agents/{name}"] = common.format(who=f"{agent.title} ({agent.description.strip()})")
    return guides


def output_folders(org: Org) -> list[str]:
    return sorted({f"{a.team or 'general'}/{a.name}" for a in org.agents.values()})


def provision(org: Org, log: Callable[[str], None] = print, graph: GraphClient | None = None) -> None:
    cfg = org.config.sharepoint
    if cfg is None:
        raise ValueError("no 'sharepoint' section in hivemind.yaml")
    if org.env.sharepoint_local_dir:
        _provision_local(org, org.root / org.env.sharepoint_local_dir, log)
    else:
        _provision_graph(org, graph or GraphClient(), org.env.sharepoint_site or cfg.site, log)


def _guide_doc(path: str, text: str) -> bytes:
    return markdown_to_docx(text, title=f"How to use '{path}'")


def _provision_local(org: Org, root: Path, log: Callable[[str], None]) -> None:
    cfg = org.config.sharepoint
    assert cfg is not None
    for path, text in rule_folder_guides(org).items():
        folder = root / cfg.rules_library / path
        folder.mkdir(parents=True, exist_ok=True)
        guide = folder / GUIDE_NAME
        if not guide.exists():
            guide.write_bytes(_guide_doc(path, text))
    for path in output_folders(org):
        (root / cfg.outputs_library / path).mkdir(parents=True, exist_ok=True)
    log(f"provisioned local SharePoint stand-in at {root}")


def _provision_graph(org: Org, graph: GraphClient, site: str, log: Callable[[str], None]) -> None:
    cfg = org.config.sharepoint
    assert cfg is not None
    site_id = graph.site_id(site)
    existing = {lst["displayName"]: lst for lst in graph.request("GET", f"/sites/{site_id}/lists").get("value", [])}

    for library in (cfg.rules_library, cfg.outputs_library):
        if library not in existing:
            graph.request(
                "POST", f"/sites/{site_id}/lists", {"displayName": library, "list": {"template": "documentLibrary"}}
            )
            log(f"created library '{library}'")

    rules_drive = graph.drive_id(site, cfg.rules_library)
    for path, text in rule_folder_guides(org).items():
        graph.ensure_folder(rules_drive, path)
        try:
            graph.download_path(rules_drive, f"{path}/{GUIDE_NAME}")
        except GraphError as exc:
            if exc.status != 404:
                raise
            graph.upload(rules_drive, f"{path}/{GUIDE_NAME}", _guide_doc(path, text))
    log(f"rules folders ready in '{cfg.rules_library}' ({len(rule_folder_guides(org))} folders)")

    outputs_drive = graph.drive_id(site, cfg.outputs_library)
    for path in output_folders(org):
        graph.ensure_folder(outputs_drive, path)
    log(f"output folders ready in '{cfg.outputs_library}'")

    agents = sorted(org.active_agents())
    columns = {name: dict(definition) for name, (_, definition) in COLUMNS.items()}
    # People pick the agent from a drop-down of active agents.
    columns["AssignedAgent"] = {"choice": {"choices": agents}, "indexed": True}
    if cfg.tasks_list not in existing:
        graph.request(
            "POST",
            f"/sites/{site_id}/lists",
            {
                "displayName": cfg.tasks_list,
                "list": {"template": "genericList"},
                "columns": [{"name": name, **definition} for name, definition in columns.items()],
            },
        )
        log(f"created list '{cfg.tasks_list}'")
        return
    list_id = existing[cfg.tasks_list]["id"]
    current = {c["name"]: c for c in graph.request("GET", f"/sites/{site_id}/lists/{list_id}/columns")["value"]}
    for name, definition in columns.items():
        if name not in current:
            graph.request("POST", f"/sites/{site_id}/lists/{list_id}/columns", {"name": name, **definition})
            log(f"added column '{name}' to '{cfg.tasks_list}'")
    if "AssignedAgent" in current:
        column_id = current["AssignedAgent"]["id"]
        graph.request("PATCH", f"/sites/{site_id}/lists/{list_id}/columns/{column_id}", {"choice": {"choices": agents}})
    log(f"'{cfg.tasks_list}' agent drop-down: {', '.join(agents)}")
