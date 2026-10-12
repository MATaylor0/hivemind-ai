"""Graduated autonomy: what an agent may do on its own, enforced in the compiled definition.

The level in the agent spec is capped by the environment (``max_autonomy``), then applied to the agent's tools:

| Level              | Write tools                                     | Operating instructions                  |
|--------------------|-------------------------------------------------|-----------------------------------------|
| shadow             | removed                                         | do the work, state what you would do    |
| suggest            | removed                                         | recommend actions for a person to take  |
| approve            | MCP: require_approval always; others removed    | act, but each change is approved        |
| autonomous         | as catalogued                                   | act within your tools and policies      |
| autonomous_sampled | as catalogued                                   | as autonomous; a sample is reviewed     |

Read tools and knowledge are unaffected. Write tools that cannot be approval-gated (OpenAPI, Azure Functions,
raw definitions) are removed below ``autonomous`` rather than given to the agent ungated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .loader import Org
from .schema import AgentSpec, AutonomyLevel, ToolDef

APPROVABLE_KINDS = {"mcp", "fabric_iq"}

MODE_TEXT = {
    AutonomyLevel.SHADOW: (
        "You are in **shadow mode**: a person is doing this job in parallel and your work is being compared with "
        "theirs. Do the full analysis and state exactly what you would do and why, but do not take any action "
        "that changes data or contacts anyone."
    ),
    AutonomyLevel.SUGGEST: (
        "You are in **suggest mode**: you recommend, a person decides and acts. End every piece of work with a "
        "clear, numbered list of recommended actions, each with the evidence for it. You cannot change data."
    ),
    AutonomyLevel.APPROVE: (
        "You **act with approval**: you may use your tools to make changes, but each change is shown to a person "
        "for approval before it happens. Explain each proposed change in one or two sentences so they can approve "
        "it quickly."
    ),
    AutonomyLevel.AUTONOMOUS: (
        "You act **autonomously** within your tools and the organisation's policies. You own the outcome: check "
        "your work before finishing, and stop and say so when something is outside your authority or uncertain."
    ),
    AutonomyLevel.AUTONOMOUS_SAMPLED: (
        "You act **autonomously** within your tools and the organisation's policies, and a sample of your work is "
        "reviewed by people afterwards. Record the reasoning behind each decision so a reviewer can follow it."
    ),
}


@dataclass
class AutonomyDecision:
    declared: AutonomyLevel
    effective: AutonomyLevel
    capped_by_environment: bool
    removed_tools: list[str] = field(default_factory=list)
    gated_tools: list[str] = field(default_factory=list)


def effective_level(org: Org, agent: AgentSpec) -> tuple[AutonomyLevel, bool]:
    declared = agent.autonomy.level
    ceiling = org.env.max_autonomy
    if declared.rank > ceiling.rank:
        return ceiling, True
    return declared, False


def apply(org: Org, agent: AgentSpec, tools: list[ToolDef]) -> tuple[list[ToolDef], AutonomyDecision]:
    """Filter or gate ``tools`` for the agent's effective autonomy level."""
    level, capped = effective_level(org, agent)
    decision = AutonomyDecision(agent.autonomy.level, level, capped)
    kept: list[ToolDef] = []
    for tool in tools:
        if tool.access != "write" or level.rank >= AutonomyLevel.AUTONOMOUS.rank:
            kept.append(tool)
        elif level == AutonomyLevel.APPROVE and tool.kind in APPROVABLE_KINDS:
            kept.append(tool.model_copy(update={"require_approval": "always"}))
            decision.gated_tools.append(tool.id)
        else:
            decision.removed_tools.append(tool.id)
    return kept, decision


def mode_section(decision: AutonomyDecision) -> str:
    text = MODE_TEXT[decision.effective]
    if decision.removed_tools:
        text += f"\n\nTools held back at this level: {', '.join(f'`{t}`' for t in decision.removed_tools)}."
    return text


def metadata(agent: AgentSpec, decision: AutonomyDecision) -> dict[str, Any]:
    out = {"hm_autonomy": decision.effective.value}
    if decision.effective == AutonomyLevel.AUTONOMOUS_SAMPLED and agent.autonomy.sample_rate:
        out["hm_sample_rate"] = f"{agent.autonomy.sample_rate:g}"
    return out
