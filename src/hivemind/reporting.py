"""Fleet reports: what agents exist, who owns them, what they can touch and what they may spend.

Runtime usage and cost come from Foundry tracing in Application Insights (see observability/); this
module reports the *declared* fleet and, when given deployed state, flags drift.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass

from .compiler import effective_tool_ids, foundry_name, resolve_model
from .hierarchy import OrgChart
from .loader import Org
from .providers.base import DeployedAgent


@dataclass
class InventoryRow:
    name: str
    title: str
    role: str
    tier: str
    team: str
    owner: str
    reports_to: str
    reviewed_by: str
    model: str
    tools: list[str]
    write_tools: list[str]
    channels: list[str]
    schedules: list[str]
    monthly_budget_usd: float | None
    cost_center: str
    enabled: bool
    deployed_version: str = ""
    status: str = ""


def inventory(org: Org, deployed: dict[str, DeployedAgent] | None = None) -> list[InventoryRow]:
    rows = []
    active = set(org.active_agents())
    for name, agent in sorted(org.agents.items()):
        tool_ids = effective_tool_ids(org, agent)
        write_tools = [t for t in tool_ids if t in org.tools and org.tools[t].access == "write"]
        channels = ["api"] if agent.channels.api else []
        if agent.channels.m365.enabled:
            channels.append(f"m365:{agent.channels.m365.scope}")
        row = InventoryRow(
            name=name,
            title=agent.title,
            role=agent.role,
            tier=org.roles[agent.role].tier.value,
            team=agent.team or "",
            owner=agent.owner or "",
            reports_to=agent.reports_to or "",
            reviewed_by=agent.reviewed_by or "",
            model=resolve_model(org, agent),
            tools=tool_ids,
            write_tools=write_tools,
            channels=channels,
            schedules=[f"{s.name} ({s.cron})" for s in agent.schedules],
            monthly_budget_usd=agent.budget.monthly_usd,
            cost_center=agent.budget.cost_center or "",
            enabled=name in active,
        )
        if deployed is not None:
            current = deployed.get(foundry_name(org, name))
            row.deployed_version = current.version if current else ""
            row.status = "deployed" if current else ("not deployed" if row.enabled else "disabled")
        rows.append(row)
    return rows


def render_inventory(rows: list[InventoryRow], fmt: str = "table") -> str:
    if fmt == "json":
        return json.dumps([asdict(r) for r in rows], indent=2)
    headers = ["Agent", "Tier", "Team", "Reports to", "Owner", "Model", "Tools", "Budget/mo", "Status"]

    def cells(r: InventoryRow) -> list[str]:
        tools = f"{len(r.tools)}" + (f" ({len(r.write_tools)} write)" if r.write_tools else "")
        budget = f"${r.monthly_budget_usd:,.0f}" if r.monthly_budget_usd is not None else "-"
        status = r.status or ("enabled" if r.enabled else "disabled")
        if r.deployed_version:
            status += f" v{r.deployed_version}"
        return [r.name, r.tier, r.team or "-", r.reports_to or "-", r.owner or "-", r.model, tools, budget, status]

    table = [headers] + [cells(r) for r in rows]
    if fmt == "markdown":
        out = ["| " + " | ".join(table[0]) + " |", "|" + "---|" * len(headers)]
        out += ["| " + " | ".join(row) + " |" for row in table[1:]]
        return "\n".join(out)
    widths = [max(len(row[i]) for row in table) for i in range(len(headers))]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in table]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def budget_summary(org: Org) -> str:
    by_team: dict[str, float] = defaultdict(float)
    by_cc: dict[str, float] = defaultdict(float)
    unbudgeted: list[str] = []
    for name, agent in org.active_agents().items():
        if agent.budget.monthly_usd is None:
            unbudgeted.append(name)
            continue
        by_team[agent.team or "(no team)"] += agent.budget.monthly_usd
        by_cc[agent.budget.cost_center or "(none)"] += agent.budget.monthly_usd
    lines = ["Monthly budget by team:"]
    lines += [f"  {team:<24} ${amount:>10,.2f}" for team, amount in sorted(by_team.items())]
    lines.append(f"  {'TOTAL':<24} ${sum(by_team.values()):>10,.2f}")
    lines.append("\nMonthly budget by cost centre:")
    lines += [f"  {cc:<24} ${amount:>10,.2f}" for cc, amount in sorted(by_cc.items())]
    if unbudgeted:
        lines.append(f"\nAgents without a budget: {', '.join(sorted(unbudgeted))}")
    return "\n".join(lines)


def tiers(org: Org) -> dict:
    return {n: org.roles[a.role].tier for n, a in org.agents.items() if a.role in org.roles}


def org_chart(org: Org) -> OrgChart:
    return OrgChart(org.agents)
