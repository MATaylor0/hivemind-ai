"""The HiveMind SharePoint site as seen by the framework: rules in, outputs out.

Rules library layout (created by ``hivemind sharepoint provision``)::

    Agent Rules/
      Organisation/            -> every agent
      Roles/<role>/            -> agents with that role
      Teams/<team>/            -> agents in that team
      Agents/<agent>/          -> one agent

Drop a Word document (or .md/.txt) into a folder and the next deployment adds it to the matching layer of the
agent's instructions, in file-name order (prefix with 01-, 02- to control order). Turn on content approval in the
library so only approved versions reach the agents.

Outputs library layout::

    Agent Outputs/<team>/<agent>/<yyyy-mm>/<title>.docx
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .docx import docx_to_markdown, markdown_to_docx
from .graph import GraphClient
from .loader import Org

RULE_SUFFIXES = (".md", ".txt", ".docx")


@dataclass
class RuleFile:
    name: str
    text: str
    ref: str  # where it came from, recorded in the build's instruction sources


@dataclass
class SavedOutput:
    path: str
    url: str


class Workspace(Protocol):
    def rules(self, folder: str) -> list[RuleFile]: ...

    def save_output(self, path: str, content: bytes) -> SavedOutput: ...


def _decode(name: str, data: bytes) -> str:
    if name.lower().endswith(".docx"):
        return docx_to_markdown(data)
    return data.decode("utf-8-sig")


def _is_rule(name: str) -> bool:
    return name.lower().endswith(RULE_SUFFIXES) and not name.startswith(("~$", ".", "_"))


class LocalWorkspace:
    """A folder standing in for the SharePoint site (local environment, tests, demos)."""

    def __init__(self, root: Path, rules_library: str, outputs_library: str):
        self.rules_root = root / rules_library
        self.outputs_root = root / outputs_library

    def rules(self, folder: str) -> list[RuleFile]:
        directory = self.rules_root / folder
        if not directory.is_dir():
            return []
        files = sorted(p for p in directory.iterdir() if p.is_file() and _is_rule(p.name))
        return [RuleFile(p.name, _decode(p.name, p.read_bytes()), f"local:{folder}/{p.name}") for p in files]

    def save_output(self, path: str, content: bytes) -> SavedOutput:
        target = self.outputs_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return SavedOutput(path, target.as_uri())


class SharePointWorkspace:
    def __init__(self, graph: GraphClient, site: str, rules_library: str, outputs_library: str):
        self.graph = graph
        self.site = site
        self.rules_library = rules_library
        self.outputs_library = outputs_library

    def rules(self, folder: str) -> list[RuleFile]:
        drive = self.graph.drive_id(self.site, self.rules_library)
        items = sorted(
            (i for i in self.graph.children(drive, folder) if not i.is_folder and _is_rule(i.name)),
            key=lambda i: i.name,
        )
        return [
            RuleFile(i.name, _decode(i.name, self.graph.download(drive, i.id)), f"sharepoint:{folder}/{i.name}")
            for i in items
        ]

    def save_output(self, path: str, content: bytes) -> SavedOutput:
        drive = self.graph.drive_id(self.site, self.outputs_library)
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        if folder:
            self.graph.ensure_folder(drive, folder)
        item = self.graph.upload(drive, path, content)
        return SavedOutput(path, item.web_url)


def workspace_for(org: Org, graph: GraphClient | None = None) -> Workspace | None:
    cfg = org.config.sharepoint
    if cfg is None:
        return None
    env = org.env
    if env.sharepoint_local_dir:
        return LocalWorkspace(org.root / env.sharepoint_local_dir, cfg.rules_library, cfg.outputs_library)
    if env.provider == "local":
        return None
    return SharePointWorkspace(
        graph or GraphClient(), env.sharepoint_site or cfg.site, cfg.rules_library, cfg.outputs_library
    )


def rule_folders(org: Org, agent_name: str) -> dict[str, str]:
    """Instruction layer -> rules folder for that layer."""
    agent = org.agents[agent_name]
    folders = {"org": "Organisation", "role": f"Roles/{agent.role}", "agent": f"Agents/{agent.name}"}
    if agent.team:
        folders["team"] = f"Teams/{agent.team}"
    return folders


def output_path(org: Org, agent_name: str, title: str, fmt: str, now: datetime | None = None) -> str:
    agent = org.agents[agent_name]
    now = now or datetime.now(UTC)
    safe = re.sub(r"[^\w\- ]+", "", title).strip()[:80] or "output"
    return f"{agent.team or 'general'}/{agent.name}/{now:%Y-%m}/{now:%Y%m%d-%H%M} {safe}.{fmt}"


def render_output(content: str, title: str, fmt: str) -> bytes:
    if fmt == "docx":
        return markdown_to_docx(content, title=title)
    return f"# {title}\n\n{content.strip()}\n".encode()
