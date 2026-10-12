"""Processes: end-to-end business processes run by agents, with people at the approval points.

A process is a Microsoft Agent Framework *declarative workflow* (YAML in Microsoft's own format, ``kind:
Workflow``) plus a small HiveMind spec (owner, goal, triggers, budget). HiveMind doesn't invent an orchestration
language. It:

* validates the workflow against the organisation: every ``InvokeAzureAgent`` step names an active agent, and
  agents below ``autonomous`` are followed by a human step (``RequestExternalInput`` / ``Question``) before the
  process ends;
* rewrites agent names for the environment (``ap-clerk`` -> ``dev-ap-clerk``);
* packages it with a generic host (``hivemind.runtime.process_host``) that azd deploys as a Foundry hosted agent;
* runs it locally against the local provider (``hivemind process run``) for testing.

Foundry's own workflow agents are being retired (1 December 2026) in favour of Agent Framework, which is why
processes target Agent Framework.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .loader import Org
from .schema import AutonomyLevel, ProcessSpec

HUMAN_STEPS = {"RequestExternalInput", "Question"}


@dataclass
class Step:
    id: str
    kind: str
    agent: str | None = None


@dataclass
class ProcessInfo:
    spec: ProcessSpec
    workflow: dict[str, Any]
    steps: list[Step] = field(default_factory=list)

    @property
    def agents(self) -> list[str]:
        return list(dict.fromkeys(s.agent for s in self.steps if s.agent))

    @property
    def human_steps(self) -> list[Step]:
        return [s for s in self.steps if s.kind in HUMAN_STEPS]


class ProcessError(Exception):
    pass


def _agent_name(action: dict[str, Any]) -> str | None:
    agent = action.get("agent")
    if isinstance(agent, dict):
        name = agent.get("name")
        return str(name) if name else None
    if isinstance(agent, str):
        return agent
    name = action.get("agentName")
    return str(name) if name else None


def _walk(actions: Any) -> Iterator[dict[str, Any]]:
    """Every action, including those nested in If / ConditionGroup / Foreach branches."""
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        yield action
        for key in ("actions", "elseActions"):
            yield from _walk(action.get(key))
        for condition in action.get("conditions", []) or []:
            if isinstance(condition, dict):
                yield from _walk(condition.get("actions"))


def workflow_path(org: Org, spec: ProcessSpec) -> Path:
    path = (org.root / spec.workflow).resolve()
    if org.root not in path.parents:
        raise ProcessError(f"process '{spec.name}': workflow path escapes the org directory")
    return path


def load_process(org: Org, spec: ProcessSpec) -> ProcessInfo:
    path = workflow_path(org, spec)
    if not path.is_file():
        raise ProcessError(f"process '{spec.name}': workflow file not found: {spec.workflow}")
    try:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProcessError(f"process '{spec.name}': invalid YAML in {spec.workflow}: {exc}") from exc
    if not isinstance(workflow, dict) or workflow.get("kind") != "Workflow":
        raise ProcessError(f"process '{spec.name}': {spec.workflow} must be an Agent Framework 'kind: Workflow'")
    trigger = workflow.get("trigger")
    if not isinstance(trigger, dict) or not isinstance(trigger.get("actions"), list):
        raise ProcessError(f"process '{spec.name}': workflow needs a trigger with actions")
    steps = [
        Step(
            id=str(a.get("id", "")),
            kind=str(a.get("kind", "")),
            agent=_agent_name(a) if a.get("kind") == "InvokeAzureAgent" else None,
        )
        for a in _walk(trigger["actions"])
    ]
    return ProcessInfo(spec, workflow, steps)


def processes_for_agent(org: Org, agent_name: str) -> list[ProcessInfo]:
    out = []
    for spec in org.active_processes().values():
        try:
            info = load_process(org, spec)
        except ProcessError:
            continue  # reported by policy checks
        if agent_name in info.agents:
            out.append(info)
    return out


def check_process(org: Org, info: ProcessInfo) -> list[tuple[str, str]]:
    """(level, message) findings for one process."""
    from .autonomy import effective_level

    findings: list[tuple[str, str]] = []
    if not info.steps:
        findings.append(("error", "workflow has no actions"))
    active = org.active_agents()
    supervised: list[str] = []
    for step in info.steps:
        if step.kind == "InvokeAzureAgent" and not step.agent:
            findings.append(("error", f"step '{step.id}': InvokeAzureAgent without an agent name"))
            continue
        if step.agent is None:
            continue
        if step.agent not in org.agents:
            findings.append(("error", f"step '{step.id}': unknown agent '{step.agent}'"))
            continue
        if step.agent not in active:
            findings.append(("error", f"step '{step.id}': agent '{step.agent}' is disabled in this environment"))
            continue
        level, _ = effective_level(org, org.agents[step.agent])
        if level == AutonomyLevel.SHADOW:
            findings.append(
                (
                    "warning",
                    f"step '{step.id}': '{step.agent}' is in shadow mode; its output is not "
                    "meant to drive a live process",
                )
            )
        if level.rank < AutonomyLevel.AUTONOMOUS.rank:
            supervised.append(step.agent)

    if supervised and not info.human_steps:
        findings.append(
            (
                "error",
                f"agents below 'autonomous' ({', '.join(dict.fromkeys(supervised))}) take part, so the workflow "
                "needs a human step (RequestExternalInput or Question) where a person reviews or approves their work",
            )
        )
    if not info.spec.owner and org.config.policies.require_owner:
        findings.append(("error", "owner is required (an accountable human)"))
    if info.spec.goal and info.spec.goal not in org.config.goals:
        findings.append(("error", f"unknown goal '{info.spec.goal}'"))
    return findings


def rename_agents(workflow: dict[str, Any], rename: Callable[[str], str]) -> dict[str, Any]:
    """A copy of ``workflow`` with every InvokeAzureAgent target renamed (environment prefixes)."""
    import copy

    out = copy.deepcopy(workflow)
    for action in _walk(out.get("trigger", {}).get("actions")):
        if action.get("kind") != "InvokeAzureAgent":
            continue
        name = _agent_name(action)
        if not name:
            continue
        if isinstance(action.get("agent"), dict):
            action["agent"]["name"] = rename(name)
        elif "agent" in action:
            action["agent"] = rename(name)
        else:
            action["agentName"] = rename(name)
    return out


# --------------------------------------------------------------------------------------------------
# Local execution (needs the 'processes' extra: agent-framework-declarative)
# --------------------------------------------------------------------------------------------------


class _DispatchAgent:
    """Adapts ``invoke(agent, text) -> text`` (e.g. a HiveMind provider) to Agent Framework's agent protocol."""

    def __init__(self, name: str, invoke: Callable[[str, str], str]):
        self.id = name
        self.name = name
        self.description = name
        self._invoke = invoke

    async def run(self, messages=None, *, stream=False, session=None, **kwargs):  # noqa: ANN001 - AF protocol
        from agent_framework import AgentResponse, Message

        text = "\n\n".join(_message_text(m) for m in (messages or []) if _message_text(m))
        reply = self._invoke(self.name, text)
        return AgentResponse(messages=[Message("assistant", [reply])], response_id=f"{self.name}-local")

    def create_session(self, *, session_id=None):  # noqa: ANN001
        from agent_framework import AgentSession

        return AgentSession(session_id=session_id)

    def get_session(self, service_session_id, *, session_id=None):  # noqa: ANN001
        from agent_framework import AgentSession

        return AgentSession(service_session_id=service_session_id, session_id=session_id)


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    text = getattr(message, "text", None)
    return text if isinstance(text, str) else ""


@dataclass
class RunResult:
    status: str  # "completed" | "awaiting_input"
    outputs: list[str]
    requests: list[dict[str, str]]


class LocalProcessRun:
    """Run a process in this Python process, answering human steps from a callback (or stopping at them)."""

    def __init__(self, org: Org, name: str, invoke: Callable[[str, str], str]):
        try:
            from agent_framework_declarative import WorkflowFactory
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProcessError(
                "running processes needs the 'processes' extra: pip install 'hivemind-ai[processes]'"
            ) from exc
        spec = org.processes[name]
        self.info = load_process(org, spec)
        agents = {a: _DispatchAgent(a, invoke) for a in self.info.agents}
        with warnings.catch_warnings():  # Agent Framework marks declarative agents as experimental
            warnings.simplefilter("ignore")
            self.workflow = WorkflowFactory(agents=agents).create_workflow_from_definition(self.info.workflow)

    async def run(self, text: str, answer: Callable[[str], str | None] | None = None) -> RunResult:
        from agent_framework_declarative import ExternalInputResponse

        result = await self.workflow.run(text)
        outputs = [str(o) for o in result.get_outputs()]
        while True:
            requests = result.get_request_info_events()
            if not requests:
                return RunResult("completed", outputs, [])
            pending = [{"id": r.request_id, "prompt": str(getattr(r.data, "message", "") or r.data)} for r in requests]
            if answer is None:
                return RunResult("awaiting_input", outputs, pending)
            responses = {}
            for request in pending:
                reply = answer(request["prompt"])
                if reply is None:
                    return RunResult("awaiting_input", outputs, pending)
                responses[request["id"]] = ExternalInputResponse(user_input=reply, value=reply)
            result = await self.workflow.run(responses=responses)
            outputs.extend(str(o) for o in result.get_outputs())
