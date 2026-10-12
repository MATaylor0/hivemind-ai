"""Read models for the admin console. Pure functions over the same code the CLI uses, so the console can never
disagree with ``hivemind validate`` / ``plan`` / ``tasks``."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..compiler import compile_agent, compile_org, effective_tool_ids, foundry_name, resolve_tool
from ..hierarchy import OrgChart
from ..instructions import SourceResolver, goal_chain
from ..loader import LoadError, Org, load_org
from ..planner import make_plan
from ..policy import check_org
from ..reporting import inventory
from ..workspace import LocalWorkspace, workspace_for


def _load(org_dir: Path, env: str | None) -> Org:
    return load_org(org_dir, env=env or None)


def _resolver(org: Org, fetch_remote: bool) -> SourceResolver:
    workspace = workspace_for(org)
    offline = not fetch_remote and not isinstance(workspace, LocalWorkspace)
    return SourceResolver(org.root, offline=offline, workspace=workspace)


def environments(org: Org) -> list[dict[str, Any]]:
    return [
        {"name": name, "provider": env.provider, "protected": env.protected, "prefix": env.name_prefix}
        for name, env in org.config.environments.items()
    ]


def _chart_node(chart: OrgChart, org: Org, name: str, seen: set[str]) -> dict[str, Any]:
    agent = chart.agents[name]
    node = {
        "name": name,
        "title": agent.title,
        "tier": org.roles[agent.role].tier.value if agent.role in org.roles else "",
        "team": agent.team or "",
        "reviewed_by": agent.reviewed_by or "",
        "collaborates_with": agent.collaborates_with,
        "enabled": agent.enabled,
        "children": [],
    }
    if name not in seen:
        node["children"] = [_chart_node(chart, org, r.name, seen | {name}) for r in chart.reports(name)]
    return node


def _goals(org: Org) -> list[dict[str, Any]]:
    def node(goal_id: str) -> dict[str, Any]:
        goal = org.config.goals[goal_id]
        children = [g for g, d in org.config.goals.items() if d.parent == goal_id]
        agents = sorted(n for n, a in org.agents.items() if goal_id in a.goals)
        return {
            "id": goal_id,
            "title": goal.title,
            "owner": goal.owner or "",
            "agents": agents,
            "children": [node(c) for c in sorted(children)],
        }

    roots = [g for g, d in org.config.goals.items() if not d.parent or d.parent not in org.config.goals]
    return [node(g) for g in sorted(roots)]


def overview(org_dir: Path, env: str | None) -> dict[str, Any]:
    try:
        org = _load(org_dir, env)
    except LoadError as exc:
        # Keep the environment picker usable, e.g. when an environment's variables aren't set on this machine.
        try:
            envs = environments(_load(org_dir, None))
        except LoadError:
            envs = []
        return {"error": str(exc), "environments": envs}
    findings = check_org(org)
    chart = OrgChart(org.agents)
    rows = inventory(org)
    budget = sum(r.monthly_budget_usd or 0 for r in rows if r.enabled)
    return {
        "org": {"name": org.config.org.name, "description": org.config.org.description, "root": str(org.root)},
        "env": env,
        "environments": environments(org),
        "counts": {
            "agents": len(org.agents),
            "active_agents": len(org.active_agents()),
            "teams": len(org.config.teams),
            "roles": len(org.roles),
            "tools": len(org.tools),
            "knowledge": len(org.knowledge),
            "goals": len(org.config.goals),
            "errors": sum(f.level == "error" for f in findings),
            "warnings": sum(f.level == "warning" for f in findings),
            "monthly_budget_usd": budget,
        },
        "findings": [{"level": f.level, "subject": f.subject, "message": f.message} for f in findings],
        "chart": [_chart_node(chart, org, r.name, set()) for r in chart.roots()],
        "goals": _goals(org),
        "teams": [
            {"name": t, "description": d.description, "agents": sorted(n for n, a in org.agents.items() if a.team == t)}
            for t, d in sorted(org.config.teams.items())
        ],
        "agents": [asdict(r) for r in rows],
        "sharepoint": org.config.sharepoint.model_dump() if org.config.sharepoint else None,
    }


def agent_detail(org_dir: Path, env: str | None, name: str, fetch_remote: bool = False) -> dict[str, Any]:
    try:
        org = _load(org_dir, env)
    except LoadError as exc:
        return {"error": str(exc)}
    if name not in org.agents:
        return {"error": f"unknown agent '{name}'"}
    agent = org.agents[name]
    out: dict[str, Any] = {
        "name": name,
        "title": agent.title,
        "description": agent.description,
        "spec": agent.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        "source_file": str(org.sources.get(f"AgentSpec:{name}", "")),
        "goals": [{"id": g, "chain": [org.config.goals[c].title for c in goal_chain(org, g)]} for g in agent.goals],
        "findings": [{"level": f.level, "message": f.message} for f in check_org(org) if f.subject == f"agent:{name}"],
    }
    tools = []
    for tool_id in effective_tool_ids(org, agent):
        try:
            tool = resolve_tool(org, tool_id)
        except Exception:  # noqa: BLE001 - reported by policy findings
            continue
        tools.append(
            {
                "id": tool.id,
                "kind": tool.kind,
                "access": tool.access,
                "description": tool.description,
                "user_context": tool.user_context,
                "knowledge": tool_id in org.knowledge,
            }
        )
    out["tools"] = tools
    try:
        build = compile_agent(org, name, _resolver(org, fetch_remote))
    except Exception as exc:  # noqa: BLE001 - shown in the console rather than crashing it
        out["compile_error"] = str(exc)
        return out
    out["build"] = {
        "name": build.name,
        "model": build.definition.get("model"),
        "hash": build.content_hash,
        "instructions": build.instructions,
        "sources": build.instruction_sources,
        "definition": build.definition,
        "metadata": build.metadata,
        "memory_store": build.memory_store.name if build.memory_store else None,
        "remote_sources_fetched": fetch_remote,
    }
    return out


def plan(org_dir: Path, env: str) -> dict[str, Any]:
    from ..providers import provider_for

    try:
        org = _load(org_dir, env)
        builds = compile_org(org, _resolver(org, fetch_remote=True))
        deployed = provider_for(org).list_managed()
    except Exception as exc:  # noqa: BLE001 - e.g. no Azure credentials on this machine
        return {"error": f"{type(exc).__name__}: {exc}"}
    result = make_plan(builds, deployed)
    return {
        "env": env,
        "items": [
            {
                "action": i.action,
                "name": i.name,
                "reason": i.reason,
                "deployed_version": i.deployed.version if i.deployed else "",
                "deployed_hash": i.deployed.content_hash if i.deployed else "",
                "new_hash": i.build.content_hash if i.build else "",
            }
            for i in sorted(result.items, key=lambda i: (i.action == "unchanged", i.name))
        ],
        "summary": {a: len(result.by_action(a)) for a in ("create", "update", "delete", "unchanged")},
    }


def tasks(org_dir: Path, env: str, limit: int = 200) -> dict[str, Any]:
    from ..tasks import task_store_for

    try:
        org = _load(org_dir, env)
        items = task_store_for(org).query(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    items = sorted(items, key=lambda t: t.created_at, reverse=True)
    counts: dict[str, int] = {}
    for t in items:
        counts[t.status] = counts.get(t.status, 0) + 1
    return {
        "env": env,
        "counts": counts,
        "tasks": [t.to_dict() for t in items],
        "names": {n: foundry_name(org, n) for n in org.agents},
    }
