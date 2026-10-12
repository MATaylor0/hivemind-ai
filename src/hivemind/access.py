"""Access rules shared by the compiler (what an agent is told it can do) and the hub (what it may do).

Memory scopes
-------------
* ``org``            - implicit. Everyone reads; executives and managers write. Override in hivemind.yaml.
* ``team:<team>``    - implicit for every team. Members read/write; managers above a member read.
* ``<custom>``       - declared under ``memory.scopes`` with read/write selectors. An agent must also opt
                       in via ``memory.shared`` so it is only given scopes relevant to its job.
"""

from __future__ import annotations

from dataclasses import dataclass

from .hierarchy import OrgChart, audience_allows
from .loader import Org
from .schema import MemoryScopeDef, Tier

ORG_SCOPE = "org"
DEFAULT_ORG_SCOPE = MemoryScopeDef(
    read=["*"],
    write=[f"tier:{Tier.EXECUTIVE.value}", f"tier:{Tier.MANAGER.value}"],
    description="Organisation-wide facts, decisions and announcements.",
)


@dataclass(frozen=True)
class ScopeAccess:
    scope: str
    read: bool
    write: bool
    description: str = ""


def _tier(org: Org, name: str) -> Tier:
    return org.roles[org.agents[name].role].tier


def memory_access(org: Org, name: str) -> dict[str, ScopeAccess]:
    """Every shared-memory scope ``name`` can read or write."""
    agent = org.agents[name]
    tier = _tier(org, name)
    chart = OrgChart(org.active_agents() if name in org.active_agents() else org.agents)
    out: dict[str, ScopeAccess] = {}

    org_scope = org.config.memory.scopes.get(ORG_SCOPE, DEFAULT_ORG_SCOPE)
    out[ORG_SCOPE] = ScopeAccess(
        ORG_SCOPE,
        read=audience_allows(org_scope.read, agent, tier),
        write=audience_allows(org_scope.write, agent, tier),
        description=org_scope.description,
    )

    if agent.team:
        scope = f"team:{agent.team}"
        team = org.config.teams.get(agent.team)
        out[scope] = ScopeAccess(scope, True, True, team.description if team else "")

    # Managers can read the team memory of everyone below them.
    for report in chart.descendants(name):
        if report.team and f"team:{report.team}" not in out:
            scope = f"team:{report.team}"
            out[scope] = ScopeAccess(scope, read=True, write=False, description=f"Team memory of {report.team}")

    for scope_name in agent.memory.shared:
        definition = org.config.memory.scopes.get(scope_name)
        if definition is None or scope_name == ORG_SCOPE:
            continue
        read = audience_allows(definition.read, agent, tier)
        write = audience_allows(definition.write, agent, tier)
        if read or write:
            out[scope_name] = ScopeAccess(scope_name, read, write, definition.description)

    return {k: v for k, v in out.items() if v.read or v.write}
