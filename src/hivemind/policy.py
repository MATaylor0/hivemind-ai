"""Governance checks run by ``hivemind validate`` and before every ``plan``/``apply``.

Errors block deployment. Warnings are surfaced in CI so reviewers can make a call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .autonomy import effective_level
from .compiler import effective_tool_ids, resolve_model
from .evals import EvalDataError, dataset_rows, gate
from .hierarchy import OrgChart, audience_allows
from .loader import Org
from .processes import ProcessError, check_process, load_process
from .schema import AutonomyLevel, Tier

Level = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    level: Level
    subject: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.subject}: {self.message}"


def check_org(org: Org) -> list[Finding]:
    findings: list[Finding] = []

    def error(subject: str, message: str) -> None:
        findings.append(Finding("error", subject, message))

    def warn(subject: str, message: str) -> None:
        findings.append(Finding("warning", subject, message))

    cfg = org.config
    policies = cfg.policies
    chart = OrgChart(org.agents)

    if not org.agents:
        warn("org", "no agents defined under agents/")

    for ref in [*cfg.defaults.tools, *cfg.defaults.knowledge]:
        if ref not in org.tools and ref not in org.knowledge:
            error("hivemind.yaml", f"defaults reference unknown tool/knowledge '{ref}'")
    for team_name, team in cfg.teams.items():
        for ref in [*team.tools, *team.knowledge]:
            if ref not in org.tools and ref not in org.knowledge:
                error(f"team:{team_name}", f"references unknown tool/knowledge '{ref}'")
    for role in org.roles.values():
        for ref in [*role.tools, *role.knowledge]:
            if ref not in org.tools and ref not in org.knowledge:
                error(f"role:{role.id}", f"references unknown tool/knowledge '{ref}'")

    for goal_id, goal in cfg.goals.items():
        if goal.parent and goal.parent not in cfg.goals:
            error(f"goal:{goal_id}", f"parent '{goal.parent}' is not a defined goal")
        seen, current = {goal_id}, goal.parent
        while current in cfg.goals:
            if current in seen:
                error(f"goal:{goal_id}", "goal hierarchy has a cycle")
                break
            seen.add(current)
            current = cfg.goals[current].parent

    for cycle in chart.find_cycles():
        error("org chart", f"reporting cycle: {' -> '.join(cycle)}")

    for name, agent in sorted(org.agents.items()):
        subject = f"agent:{name}"

        if agent.role not in org.roles:
            error(subject, f"unknown role '{agent.role}' (defined: {', '.join(sorted(org.roles)) or 'none'})")
            continue
        tier = org.roles[agent.role].tier

        if agent.team and agent.team not in cfg.teams:
            error(subject, f"unknown team '{agent.team}'")

        for field_name in ("reports_to", "reviewed_by"):
            target = getattr(agent, field_name)
            if target and target not in org.agents:
                error(subject, f"{field_name} '{target}' is not a defined agent")
            elif target == name:
                error(subject, f"{field_name} cannot be the agent itself")
        for collaborator in agent.collaborates_with:
            if collaborator not in org.agents:
                error(subject, f"collaborates_with '{collaborator}' is not a defined agent")

        reviewer = org.agents.get(agent.reviewed_by or "")
        if reviewer and reviewer.role in org.roles and org.roles[reviewer.role].tier == Tier.WORKER:
            warn(subject, f"reviewer '{reviewer.name}' is a worker; reviewers are usually reviewer or manager tier")

        if tier == Tier.EXECUTIVE and agent.reports_to:
            warn(subject, "executive-tier agents normally sit at the top of the hierarchy")
        if tier != Tier.EXECUTIVE and not agent.reports_to:
            warn(subject, "has no manager (reports_to); it will appear as a root of the org chart")
        if tier == Tier.WORKER and chart.reports(name):
            warn(subject, "worker-tier agent has direct reports; consider a manager role")
        if chart.depth(name) > policies.max_hierarchy_depth:
            error(subject, f"hierarchy depth {chart.depth(name)} exceeds max {policies.max_hierarchy_depth}")

        for goal_id in agent.goals:
            if goal_id not in cfg.goals:
                error(subject, f"unknown goal '{goal_id}'")

        if policies.require_owner and not agent.owner:
            error(subject, "owner is required (an accountable human)")
        if policies.require_budget and agent.budget.monthly_usd is None:
            error(subject, "budget.monthly_usd is required")

        model = resolve_model(org, agent)
        if cfg.models.allowed and model not in cfg.models.allowed:
            error(subject, f"model '{model}' is not in the allowed list ({', '.join(cfg.models.allowed)})")

        for ref in effective_tool_ids(org, agent):
            tool = org.tools.get(ref) or (org.knowledge[ref].as_tool() if ref in org.knowledge else None)
            if tool is None:
                error(subject, f"unknown tool/knowledge '{ref}'")
                continue
            if not audience_allows(tool.audience, agent, tier):
                error(subject, f"not in the audience of '{ref}' ({', '.join(tool.audience)})")
            # Below 'autonomous', the compiler gates or removes write tools. Fully autonomous agents need some other
            # form of oversight: a reviewer, sampled review, or approval on the tool itself.
            if (
                tool.access == "write"
                and policies.require_reviewer_for_write_tools
                and agent.autonomy.level == AutonomyLevel.AUTONOMOUS
                and not agent.reviewed_by
                and tool.require_approval != "always"
                and not (isinstance(tool.require_approval, dict) and tool.require_approval.get("always"))
            ):
                error(
                    subject,
                    f"autonomous with write tool '{ref}': add a reviewer (reviewed_by), use autonomous_sampled, "
                    "or set require_approval on the tool",
                )

        user_tools = [ref for ref in effective_tool_ids(org, agent) if _user_context(org, ref)]
        if user_tools and agent.schedules:
            warn(
                subject,
                f"user-context tools ({', '.join(user_tools)}) have no signed-in person during scheduled runs; "
                "they will only work when someone is chatting with the agent",
            )
        if user_tools and not agent.channels.m365.enabled:
            warn(
                subject,
                f"user-context tools ({', '.join(user_tools)}) need a signed-in person; publish the agent to M365 "
                "(channels.m365) or remove them",
            )

        _check_autonomy_and_evals(org, agent, error, warn)

        if agent.memory.private and agent.memory.private_scope == "agent" and agent.channels.m365.enabled:
            warn(
                subject,
                "private memory is shared across all users (private_scope: agent) but the agent is published "
                "to M365/Teams; use private_scope: user unless that is intended",
            )

        names = [s.name for s in agent.schedules]
        if len(names) != len(set(names)):
            error(subject, "schedule names must be unique per agent")
        for schedule in agent.schedules:
            if len(schedule.cron.split()) != 5:
                error(subject, f"schedule '{schedule.name}': cron must have 5 fields")

        if org.env_name:
            prefixed = f"{org.env.name_prefix}{name}"
            if len(prefixed) > 63:
                error(subject, f"name with environment prefix '{prefixed}' exceeds 63 characters")

    for name, process in sorted(org.processes.items()):
        if not process.enabled:
            continue
        try:
            info = load_process(org, process)
        except ProcessError as exc:
            error(f"process:{name}", str(exc))
            continue
        for level, message in check_process(org, info):
            (error if level == "error" else warn)(f"process:{name}", message)
        names = [t.name for t in process.triggers]
        if len(names) != len(set(names)):
            error(f"process:{name}", "trigger names must be unique")

    clash = set(org.agents) & {f"process-{p}" for p in org.processes}
    if clash:
        error("org", f"agent names clash with process host names: {', '.join(sorted(clash))}")

    return findings


def _check_autonomy_and_evals(org: Org, agent, error, warn) -> None:  # noqa: ANN001 - local reporters
    subject = f"agent:{agent.name}"
    level = agent.autonomy.level
    required = gate(org, agent)
    if level == AutonomyLevel.AUTONOMOUS_SAMPLED and not agent.autonomy.sample_rate:
        error(subject, "autonomous_sampled needs autonomy.sample_rate (share of runs reviewed by people)")
    if required.require_approver and not agent.autonomy.approved_by:
        error(subject, f"autonomy '{level.value}' needs autonomy.approved_by (who signed off this level)")
    try:
        rows = dataset_rows(org, agent)
    except EvalDataError as exc:
        error(subject, str(exc))
        return
    if len(rows) < required.min_cases:
        error(
            subject,
            f"autonomy '{level.value}' needs an eval dataset with at least {required.min_cases} cases "
            f"(evals.dataset has {len(rows)})",
        )
    effective, capped = effective_level(org, agent)
    if capped and org.env_name:
        warn(subject, f"autonomy '{level.value}' is capped to '{effective.value}' in environment '{org.env_name}'")


def _user_context(org: Org, ref: str) -> bool:
    if ref in org.tools:
        return org.tools[ref].user_context
    if ref in org.knowledge:
        return bool(org.knowledge[ref].as_tool().user_context)
    return False


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
