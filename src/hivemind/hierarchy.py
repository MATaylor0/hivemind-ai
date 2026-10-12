"""The organisation chart: who reports to whom, who reviews whom, who may talk to whom.

The same graph drives three things, so they can never disagree:
  * the "your position in the organisation" section compiled into each agent's instructions,
  * the hub's access control (delegate down, escalate up, request review sideways),
  * the tree / mermaid views and inventory reports.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .schema import AgentSpec, Tier


def selector_matches(selector: str, agent: AgentSpec, tier: Tier | None = None) -> bool:
    """Audience selectors used by tools, knowledge and memory scopes.

    ``*`` | ``agent:<name>`` | ``team:<team>`` | ``role:<role>`` | ``tier:<tier>``
    """
    if selector == "*":
        return True
    kind, _, value = selector.partition(":")
    if kind == "agent":
        return agent.name == value
    if kind == "team":
        return agent.team == value
    if kind == "role":
        return agent.role == value
    if kind == "tier":
        return tier is not None and tier.value == value
    return False


def audience_allows(selectors: Iterable[str], agent: AgentSpec, tier: Tier | None = None) -> bool:
    return any(selector_matches(s, agent, tier) for s in selectors)


@dataclass
class OrgChart:
    agents: dict[str, AgentSpec]

    def manager(self, name: str) -> AgentSpec | None:
        boss = self.agents[name].reports_to
        return self.agents.get(boss) if boss else None

    def reports(self, name: str) -> list[AgentSpec]:
        return sorted((a for a in self.agents.values() if a.reports_to == name), key=lambda a: a.name)

    def reviewees(self, name: str) -> list[AgentSpec]:
        return sorted((a for a in self.agents.values() if a.reviewed_by == name), key=lambda a: a.name)

    def reviewer(self, name: str) -> AgentSpec | None:
        reviewer = self.agents[name].reviewed_by
        return self.agents.get(reviewer) if reviewer else None

    def collaborators(self, name: str) -> list[AgentSpec]:
        """Declared collaborators, in either direction."""
        me = self.agents[name]
        names = set(me.collaborates_with) | {a.name for a in self.agents.values() if name in a.collaborates_with}
        return [self.agents[n] for n in sorted(names) if n in self.agents and n != name]

    def chain_of_command(self, name: str) -> list[AgentSpec]:
        """Managers from the direct manager up to the top. Stops on cycles (reported by find_cycles)."""
        chain: list[AgentSpec] = []
        seen = {name}
        current = self.manager(name)
        while current is not None and current.name not in seen:
            chain.append(current)
            seen.add(current.name)
            current = self.manager(current.name)
        return chain

    def depth(self, name: str) -> int:
        return len(self.chain_of_command(name))

    def roots(self) -> list[AgentSpec]:
        return sorted(
            (a for a in self.agents.values() if not a.reports_to or a.reports_to not in self.agents),
            key=lambda a: a.name,
        )

    def descendants(self, name: str) -> list[AgentSpec]:
        out: list[AgentSpec] = []
        stack = [r.name for r in self.reports(name)]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            out.append(self.agents[current])
            stack.extend(r.name for r in self.reports(current))
        return sorted(out, key=lambda a: a.name)

    def find_cycles(self) -> list[list[str]]:
        cycles: list[list[str]] = []
        reported: set[frozenset[str]] = set()
        for start in self.agents:
            path = [start]
            current = self.agents[start].reports_to
            while current and current in self.agents:
                if current in path:
                    cycle = path[path.index(current) :]
                    key = frozenset(cycle)
                    if key not in reported:
                        reported.add(key)
                        cycles.append(cycle + [current])
                    break
                path.append(current)
                current = self.agents[current].reports_to
        return cycles

    # ---------------------------------------------------------------- access rules used by the hub

    def can_delegate(self, caller: str, target: str) -> bool:
        """Managers delegate to direct reports; anyone may hand work to a declared collaborator."""
        if target not in self.agents or caller == target:
            return False
        return self.agents[target].reports_to == caller or target in {c.name for c in self.collaborators(caller)}

    def delegation_targets(self, caller: str) -> list[AgentSpec]:
        seen: dict[str, AgentSpec] = {a.name: a for a in self.reports(caller)}
        for c in self.collaborators(caller):
            seen.setdefault(c.name, c)
        return [seen[k] for k in sorted(seen)]

    # ---------------------------------------------------------------- rendering

    def render_text(self, tiers: dict[str, Tier] | None = None) -> str:
        lines: list[str] = []

        def label(a: AgentSpec) -> str:
            tier = f" [{tiers[a.name].value}]" if tiers and a.name in tiers else ""
            extras = []
            if a.team:
                extras.append(f"team={a.team}")
            if a.reviewed_by:
                extras.append(f"reviewed by {a.reviewed_by}")
            if not a.enabled:
                extras.append("disabled")
            suffix = f"  ({', '.join(extras)})" if extras else ""
            return f"{a.name}{tier} - {a.title}{suffix}"

        def walk(a: AgentSpec, prefix: str, last: bool, seen: set[str]) -> None:
            connector = "└── " if last else "├── "
            lines.append(f"{prefix}{connector}{label(a)}")
            if a.name in seen:
                return
            seen = seen | {a.name}
            children = self.reports(a.name)
            for i, child in enumerate(children):
                walk(child, prefix + ("    " if last else "│   "), i == len(children) - 1, seen)

        roots = self.roots()
        for root in roots:
            lines.append(label(root))
            children = self.reports(root.name)
            for i, child in enumerate(children):
                walk(child, "", i == len(children) - 1, {root.name})
        return "\n".join(lines)

    def render_mermaid(self, tiers: dict[str, Tier] | None = None) -> str:
        def node_id(name: str) -> str:
            return name.replace("-", "_")

        lines = ["flowchart TD"]
        for a in sorted(self.agents.values(), key=lambda x: x.name):
            tier = tiers.get(a.name).value if tiers and a.name in tiers else ""
            lines.append(f'    {node_id(a.name)}["{a.title}<br/><small>{tier}</small>"]')
        for a in sorted(self.agents.values(), key=lambda x: x.name):
            if a.reports_to and a.reports_to in self.agents:
                lines.append(f"    {node_id(a.reports_to)} --> {node_id(a.name)}")
            if a.reviewed_by and a.reviewed_by in self.agents:
                lines.append(f"    {node_id(a.name)} -. review .-> {node_id(a.reviewed_by)}")
            for c in a.collaborates_with:
                if c in self.agents:
                    lines.append(f"    {node_id(a.name)} <-. collaborates .-> {node_id(c)}")
        return "\n".join(lines)
