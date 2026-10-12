"""Fetch instruction fragments and compose them into one system prompt.

Composition order (general to specific, so later layers refine earlier ones):

  1. Organisation   - ``defaults.instructions`` in hivemind.yaml (the shared "constitution").
  2. Role           - the role archetype's instructions (how a manager / reviewer / worker behaves).
  3. Team           - the team's instructions (domain context shared by the team).
  4. Agent          - the agent's own instructions (its specific job).
  5. Generated      - operating mode (autonomy), goals, processes, position in the org chart, tools and
                      knowledge, when to stop and escalate.

Fragments can live in the repo (``file:``), in SharePoint (``sharepoint:``) so business owners can edit
them without a pull request, at a URL, or inline. When the org has a HiveMind SharePoint site, every document
in the matching rules folder (Organisation/, Roles/<role>/, Teams/<team>/, Agents/<agent>/) is appended to its
layer automatically: business users just drop a Word document in the right folder.

Whatever the source, the composed text is hashed and versioned in Foundry, so a SharePoint edit shows up in
``hivemind plan`` as a change.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .docx import docx_to_markdown
from .graph import GraphClient, GraphError
from .hierarchy import OrgChart
from .loader import Org
from .schema import AgentSpec, InstructionSource, SharePointRef, ToolDef
from .workspace import LocalWorkspace, RuleFile, Workspace, rule_folders

Fetcher = Callable[[InstructionSource], str]


class InstructionError(Exception):
    pass


@dataclass
class ComposedInstructions:
    text: str
    sources: list[str] = field(default_factory=list)


class SourceResolver:
    """Resolves instruction sources to text. Remote fetches are cached for the life of the resolver.

    ``workspace`` is the HiveMind SharePoint site (or a local stand-in): its rules folders are added to each
    instruction layer by convention. ``offline`` skips every remote fetch (used by ``validate``).
    """

    def __init__(
        self,
        root: Path,
        offline: bool = False,
        graph: GraphClient | None = None,
        workspace: Workspace | None = None,
    ):
        self.root = root
        self.offline = offline
        self._graph = graph
        self.workspace = workspace
        self._cache: dict[str, str] = {}
        self._rules: dict[str, list[RuleFile]] = {}

    def __call__(self, source: InstructionSource) -> str:
        key = source.describe()
        if key not in self._cache:
            self._cache[key] = self._fetch(source)
        return self._cache[key]

    def rules(self, folder: str) -> list[RuleFile]:
        """Rule documents in a SharePoint rules folder (empty when no workspace or offline)."""
        if self.workspace is None or (self.offline and not isinstance(self.workspace, LocalWorkspace)):
            return []
        if folder not in self._rules:
            try:
                self._rules[folder] = self.workspace.rules(folder)
            except Exception as exc:  # noqa: BLE001 - Graph, network and sign-in failures all stop the build
                raise InstructionError(
                    f"could not read SharePoint rules folder '{folder}': {str(exc).splitlines()[0]} "
                    "(sign in with `az login`, or use --offline to build without SharePoint rules)"
                ) from exc
        return self._rules[folder]

    def _fetch(self, source: InstructionSource) -> str:
        if source.inline is not None:
            return source.inline
        if source.file is not None:
            path = (self.root / source.file).resolve()
            if self.root not in path.parents and path != self.root:
                raise InstructionError(f"instruction file '{source.file}' escapes the org directory")
            if not path.is_file():
                raise InstructionError(f"instruction file not found: {source.file}")
            if path.suffix.lower() == ".docx":
                return docx_to_markdown(path.read_bytes())
            return path.read_text(encoding="utf-8")
        if self.offline:
            return f"<!-- {source.describe()} is fetched at deploy time -->"
        if source.url is not None:
            return _http_get(source.url)
        assert source.sharepoint is not None
        return self._fetch_sharepoint(source.sharepoint)

    def _fetch_sharepoint(self, ref: SharePointRef) -> str:
        graph = self._graph or GraphClient()
        try:
            data = graph.download_path(graph.drive_id(ref.site, ref.drive), ref.path)
        except GraphError as exc:
            raise InstructionError(f"could not read sharepoint:{ref.site}/{ref.path}: {exc}") from exc
        return docx_to_markdown(data) if ref.path.lower().endswith(".docx") else data.decode("utf-8-sig")


def _http_get(url: str) -> str:
    if not url.startswith("https://"):
        raise InstructionError(f"refusing non-HTTPS instruction source: {url}")
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - https enforced above
            return response.read().decode("utf-8")
    except OSError as exc:
        raise InstructionError(f"failed to fetch {url}: {exc}") from exc


def _nest_headings(text: str, title: str) -> str:
    """Fit a business document under its ``### <file name>`` heading: drop a duplicate title, demote headings."""
    lines = text.strip().splitlines()
    if lines and re.match(r"^#\s+", lines[0]) and lines[0].lstrip("# ").strip().lower() == title.lower():
        lines = lines[1:]
    out = [re.sub(r"^(#{1,6})\s+", lambda m: "#" * min(len(m.group(1)) + 3, 6) + " ", line) for line in lines]
    return "\n".join(out).strip()


def _section(title: str, body: str) -> str:
    body = body.strip()
    return f"## {title}\n\n{body}" if body else ""


def _fragments(
    sources: list[InstructionSource], fetch: Fetcher, used: list[str], rules_folder: str | None = None
) -> str:
    parts = []
    for source in sources:
        text = fetch(source).strip()
        used.append(source.describe())
        if source.title:
            text = f"### {source.title}\n\n{text}"
        parts.append(text)
    # Business-owned rules from the SharePoint site, added after the repo-defined guidance for the layer.
    rules = getattr(fetch, "rules", None)
    if rules_folder and rules is not None:
        for rule in rules(rules_folder):
            used.append(rule.ref)
            title = rule.name.rsplit(".", 1)[0]
            parts.append(f"### {title}\n\n{_nest_headings(rule.text, title)}")
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
        lines.append("\nYour direct reports:")
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
        lines.append("\nColleagues you work closely with:")
        lines.extend(_bullet(c) for c in collaborators)
    return "\n".join(lines)


def processes_section(org: Org, agent: AgentSpec) -> str:
    from .processes import processes_for_agent

    lines = []
    for info in processes_for_agent(org, agent.name):
        steps = ", ".join(f"`{s.id}`" for s in info.steps if s.agent == agent.name)
        goal = f" (goal: {org.config.goals[info.spec.goal].title})" if info.spec.goal in org.config.goals else ""
        lines.append(f"- **{info.spec.title}**{goal}: {info.spec.description.strip()} You run step(s) {steps}.")
    if not lines:
        return ""
    return (
        "You take part in these processes. When a message comes from a process step, do that step's job "
        "completely, and say plainly if you could not, so the process can route the work to a person:\n"
        + "\n".join(lines)
    )


def escalation_section(org: Org, agent: AgentSpec) -> str:
    chart = OrgChart(org.active_agents())
    manager = chart.manager(agent.name)
    owner = agent.owner or "your owner"
    lines = [
        "Stop and hand back to a person, rather than guessing, when a request is outside your job, needs "
        "authority you don't have, conflicts with policy, or you are not confident in the answer.",
        "Say clearly what you did, what is left, why you stopped, and who should take it from here:",
    ]
    if manager:
        lines.append(f"- for decisions outside your remit: the {manager.title} (`{manager.name}`) and its owner")
    lines.append(f"- for problems with how you work: {owner}")
    return "\n".join(lines)


def capabilities_section(tools: list[ToolDef]) -> str:
    if not tools:
        return ""
    lines = ["You have these tools and knowledge sources. Prefer them over guessing:"]
    for tool in tools:
        tag = " (can change data - act carefully)" if tool.access == "write" else ""
        lines.append(f"- `{tool.id}`{tag}: {tool.description.strip() or tool.kind}")
    return "\n".join(lines)


def goal_chain(org: Org, goal_id: str) -> list[str]:
    """``goal_id`` followed by its ancestors, stopping on unknown ids or cycles."""
    chain: list[str] = []
    current: str | None = goal_id
    while current and current in org.config.goals and current not in chain:
        chain.append(current)
        current = org.config.goals[current].parent
    return chain


def goals_section(org: Org, agent: AgentSpec) -> str:
    if not agent.goals:
        return ""
    lines = ["Your work serves these organisational goals (most specific first). Use them to prioritise:"]
    for goal_id in agent.goals:
        chain = goal_chain(org, goal_id)
        if chain:
            lines.append("- " + " → ".join(org.config.goals[g].title for g in chain))
    return "\n".join(lines)


def compose(org: Org, agent: AgentSpec, tools: list[ToolDef], fetch: Fetcher, mode: str = "") -> ComposedInstructions:
    role = org.roles[agent.role]
    team = org.config.teams.get(agent.team) if agent.team else None
    used: list[str] = []
    folders = rule_folders(org, agent.name) if org.config.sharepoint else {}
    team_sources = team.instructions if team else []

    sections = [
        f"# {agent.title}\n\n{agent.description.strip()}",
        _section(
            "Organisation guidance",
            _fragments(org.config.defaults.instructions, fetch, used, folders.get("org")),
        ),
        _section(f"Your role: {role.id}", _fragments(role.instructions, fetch, used, folders.get("role"))),
        _section(f"Team: {agent.team}", _fragments(team_sources, fetch, used, folders.get("team")))
        if agent.team
        else "",
        _section("Your job", _fragments(agent.instructions, fetch, used, folders.get("agent"))),
        _section("Operating mode", mode),
        _section("Goals", goals_section(org, agent)),
        _section("Processes", processes_section(org, agent)),
        _section("Your position in the organisation", org_position_section(org, agent)),
        _section("Tools and knowledge", capabilities_section(tools)),
        _section("When to stop and escalate", escalation_section(org, agent)),
    ]
    return ComposedInstructions(text="\n\n".join(s for s in sections if s).strip() + "\n", sources=used)
