"""Fetch instruction fragments and compose them into one system prompt.

Composition order (general to specific, so later layers refine earlier ones):

  1. Organisation   - ``defaults.instructions`` in hivemind.yaml (the shared "constitution").
  2. Role           - the role archetype's instructions (how a manager / reviewer / worker behaves).
  3. Team           - the team's instructions (domain context shared by the team).
  4. Agent          - the agent's own instructions (its specific job).
  5. Generated      - position in the org chart, colleagues, tools and knowledge, hub etiquette.

Fragments can live in the repo (``file:``), in SharePoint (``sharepoint:``) so business owners can edit
them without a pull request, at a URL, or inline. Whatever the source, the composed text is hashed and
versioned in Foundry, so a SharePoint edit shows up in ``hivemind plan`` as a change.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .access import memory_access
from .hierarchy import OrgChart
from .loader import Org
from .schema import AgentSpec, InstructionSource, SharePointRef, ToolDef

Fetcher = Callable[[InstructionSource], str]


class InstructionError(Exception):
    pass


@dataclass
class ComposedInstructions:
    text: str
    sources: list[str] = field(default_factory=list)


class SourceResolver:
    """Resolves instruction sources to text. Remote fetches are cached for the life of the resolver."""

    def __init__(self, root: Path, offline: bool = False, graph_token: Callable[[], str] | None = None):
        self.root = root
        self.offline = offline
        self._graph_token = graph_token
        self._cache: dict[str, str] = {}

    def __call__(self, source: InstructionSource) -> str:
        key = source.describe()
        if key not in self._cache:
            self._cache[key] = self._fetch(source)
        return self._cache[key]

    def _fetch(self, source: InstructionSource) -> str:
        if source.inline is not None:
            return source.inline
        if source.file is not None:
            path = (self.root / source.file).resolve()
            if self.root not in path.parents and path != self.root:
                raise InstructionError(f"instruction file '{source.file}' escapes the org directory")
            if not path.is_file():
                raise InstructionError(f"instruction file not found: {source.file}")
            return path.read_text(encoding="utf-8")
        if self.offline:
            return f"<!-- {source.describe()} is fetched at deploy time -->"
        if source.url is not None:
            return _http_get(source.url)
        assert source.sharepoint is not None
        return self._fetch_sharepoint(source.sharepoint)

    def _fetch_sharepoint(self, ref: SharePointRef) -> str:
        token = self._token()
        host, _, site_path = ref.site.partition("/")
        site = json.loads(_http_get(f"https://graph.microsoft.com/v1.0/sites/{host}:/{site_path}", token))
        site_id = site["id"]
        if ref.drive:
            drives = json.loads(_http_get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives", token))
            match = [d for d in drives.get("value", []) if d.get("name") == ref.drive]
            if not match:
                raise InstructionError(f"document library '{ref.drive}' not found on {ref.site}")
            drive_url = f"https://graph.microsoft.com/v1.0/drives/{match[0]['id']}"
        else:
            drive_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive"
        item_path = urllib.parse.quote(ref.path.strip("/"))
        return _http_get(f"{drive_url}/root:/{item_path}:/content", token)

    def _token(self) -> str:
        if self._graph_token is not None:
            return self._graph_token()
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise InstructionError("SharePoint sources need the 'azure' extra: pip install hivemind-ai[azure]") from exc
        return DefaultAzureCredential().get_token("https://graph.microsoft.com/.default").token


def _http_get(url: str, token: str | None = None) -> str:
    if not url.startswith("https://"):
        raise InstructionError(f"refusing non-HTTPS instruction source: {url}")
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https enforced above
            return response.read().decode("utf-8")
    except OSError as exc:
        raise InstructionError(f"failed to fetch {url}: {exc}") from exc


def _section(title: str, body: str) -> str:
    body = body.strip()
    return f"## {title}\n\n{body}" if body else ""


def _fragments(sources: list[InstructionSource], fetch: Fetcher, used: list[str]) -> str:
    parts = []
    for source in sources:
        text = fetch(source).strip()
        used.append(source.describe())
        if source.title:
            text = f"### {source.title}\n\n{text}"
        parts.append(text)
    return "\n\n".join(p for p in parts if p)


def _bullet(agent: AgentSpec) -> str:
    return f"- **{agent.title}** (`{agent.name}`): {agent.description.strip()}"


def org_position_section(org: Org, agent: AgentSpec) -> str:
    chart = OrgChart(org.active_agents())
    lines: list[str] = [f"You are **{agent.title}** (`{agent.name}`), part of {org.config.org.name}'s HiveMind."]
    if agent.team:
        lines.append(f"Team: **{agent.team}**.")
    manager = chart.manager(agent.name)
    if manager:
        lines.append(f"\nYou report to:\n{_bullet(manager)}")
    reports = chart.reports(agent.name)
    if reports:
        lines.append("\nYour direct reports (delegate work that matches their remit):")
        lines.extend(_bullet(r) for r in reports)
    reviewer = chart.reviewer(agent.name)
    if reviewer:
        lines.append(f"\nYour work is reviewed by:\n{_bullet(reviewer)}")
    reviewees = chart.reviewees(agent.name)
    if reviewees:
        lines.append("\nYou review work produced by:")
        lines.extend(_bullet(r) for r in reviewees)
    collaborators = chart.collaborators(agent.name)
    if collaborators:
        lines.append("\nColleagues you can hand work to directly:")
        lines.extend(_bullet(c) for c in collaborators)
    return "\n".join(lines)


def hub_section(org: Org, agent: AgentSpec) -> str:
    if not org.config.hub.enabled:
        return ""
    chart = OrgChart(org.active_agents())
    rules = [
        "You are connected to the HiveMind hub (the `hivemind` tools). Use them as follows:",
    ]
    if chart.delegation_targets(agent.name):
        rules.append(
            "- `delegate_task`: hand a well-scoped task to a direct report or collaborator and use their answer."
        )
    if chart.manager(agent.name):
        rules.append(
            "- `escalate`: when a request is outside your remit, needs authority you do not have, or you are blocked."
        )
    if chart.reviewer(agent.name):
        rules.append("- `request_review`: before any irreversible or externally visible action, and for final outputs.")
    rules.append(
        "- `recall` / `remember`: shared organisational memory. Recall before starting work; remember durable "
        "facts, decisions and outcomes others will need. Never store secrets or personal data you were not "
        "asked to keep."
    )
    rules.append("- `list_colleagues`: discover who else in the organisation can help.")
    scopes = memory_access(org, agent.name)
    if scopes:
        rules.append("\nShared memory scopes available to you:")
        for access in scopes.values():
            mode = "read/write" if access.read and access.write else ("read" if access.read else "write")
            desc = f" - {access.description}" if access.description else ""
            rules.append(f"- `{access.scope}` ({mode}){desc}")
    return "\n".join(rules)


def capabilities_section(tools: list[ToolDef]) -> str:
    if not tools:
        return ""
    lines = ["You have these tools and knowledge sources. Prefer them over guessing:"]
    for tool in tools:
        tag = " (can change data - act carefully)" if tool.access == "write" else ""
        lines.append(f"- `{tool.id}`{tag}: {tool.description.strip() or tool.kind}")
    return "\n".join(lines)


def compose(org: Org, agent: AgentSpec, tools: list[ToolDef], fetch: Fetcher) -> ComposedInstructions:
    role = org.roles[agent.role]
    team = org.config.teams.get(agent.team) if agent.team else None
    used: list[str] = []

    sections = [
        f"# {agent.title}\n\n{agent.description.strip()}",
        _section("Organisation guidance", _fragments(org.config.defaults.instructions, fetch, used)),
        _section(f"Your role: {role.id}", _fragments(role.instructions, fetch, used)),
        _section(f"Team: {agent.team}", _fragments(team.instructions, fetch, used)) if team else "",
        _section("Your job", _fragments(agent.instructions, fetch, used)),
        _section("Your position in the organisation", org_position_section(org, agent)),
        _section("Tools and knowledge", capabilities_section(tools)),
        _section("Working with other agents", hub_section(org, agent)),
    ]
    return ComposedInstructions(text="\n\n".join(s for s in sections if s).strip() + "\n", sources=used)
