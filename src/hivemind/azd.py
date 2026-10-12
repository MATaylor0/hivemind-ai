"""Generate an Azure Developer CLI project (``azure.yaml``) from the organisation, for ``azd deploy``.

HiveMind owns the *operating model* (org, policies, autonomy, layered instructions, evals). Microsoft owns
deployment: the generated project uses the Foundry hosts of the azd ``azure.ai.*`` extensions:

* ``azure.ai.project``  - the Foundry project (existing ``endpoint``, or provisioned) and model deployments
* ``azure.ai.agent``    - one prompt agent per HiveMind agent (instructions compiled, autonomy applied), and one
                          hosted agent per process (an Agent Framework workflow in the HiveMind process host)
* ``azure.ai.routine``  - agent schedules and process triggers
* ``azure.ai.eval``     - each agent's golden-dataset regression eval (the promotion gate) and trace eval

The build directory is disposable; regenerate it, don't edit it.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .compiler import AgentBuild, compile_agent, foundry_name, resolve_model
from .evals import dataset_path, eval_service
from .instructions import Fetcher, SourceResolver
from .loader import Org
from .processes import load_process, rename_agents
from .schema import ProcessSpec

PROJECT_SERVICE = "foundry"
RUNTIME_DIR = Path(__file__).parent / "runtime"
SCHEMA_COMMENT = "# yaml-language-server: $schema=https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json\n"  # noqa: E501
PROCESS_HOST_REQUIREMENTS = [
    "agent-framework-declarative>=1.2,<2",
    "agent-framework-foundry>=1.14,<2",
    "azure-ai-agentserver-invocations>=1.2,<2",
    "azure-identity>=1.17",
]


@dataclass
class BuildResult:
    root: Path
    document: dict[str, Any]
    agents: list[AgentBuild] = field(default_factory=list)
    processes: list[str] = field(default_factory=list)

    @property
    def azure_yaml(self) -> Path:
        return self.root / "azure.yaml"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-") or "org"


def process_agent_name(org: Org, process: str) -> str:
    return foundry_name(org, f"process-{process}")


def project_service(org: Org) -> dict[str, Any]:
    service: dict[str, Any] = {"host": "azure.ai.project"}
    if org.env.endpoint:
        service["endpoint"] = org.env.endpoint
    if org.env.model_deployments:
        service["deployments"] = [
            {
                "name": d.name,
                "model": {"format": d.format, "name": d.model, "version": d.version},
                "sku": {"name": d.sku, "capacity": d.capacity},
            }
            for d in org.env.model_deployments
        ]
    return service


def agent_service(org: Org, build: AgentBuild) -> dict[str, Any]:
    definition = build.definition
    tools = [t for t in definition["tools"] if t.get("type") != "memory_search_preview"]
    service: dict[str, Any] = {
        "host": "azure.ai.agent",
        "project": ".",
        "uses": [PROJECT_SERVICE],
        "kind": "prompt",
        "name": build.name,
        "description": build.description,
        "model": definition["model"],
        "instructions": definition["instructions"],
    }
    if "temperature" in definition:
        service["temperature"] = definition["temperature"]
    if tools:
        service["tools"] = tools
    memory = next((t for t in definition["tools"] if t.get("type") == "memory_search_preview"), None)
    if memory and build.memory_store:
        service["memory"] = {
            "store": build.memory_store.name,
            "chatModel": build.memory_store.chat_model,
            "embeddingModel": build.memory_store.embedding_model,
            "scope": memory["scope"],
        }
    service["metadata"] = dict(build.metadata)
    return service


def _routine(uses: str, description: str, cron: str, time_zone: str, enabled: bool, action: dict[str, Any]) -> dict:
    return {
        "host": "azure.ai.routine",
        "uses": [uses],
        "description": description,
        "enabled": enabled,
        "triggers": {"default": {"type": "schedule", "cronExpression": cron, "timeZone": time_zone}},
        "action": action,
    }


def _write_process(org: Org, spec: ProcessSpec, out: Path) -> tuple[str, dict[str, Any], list[str]]:
    info = load_process(org, spec)
    project = out / "processes" / spec.name
    project.mkdir(parents=True, exist_ok=True)
    workflow = rename_agents(info.workflow, lambda name: foundry_name(org, name))
    (project / "workflow.yaml").write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    shutil.copyfile(RUNTIME_DIR / "process_host.py", project / "main.py")
    shutil.copyfile(RUNTIME_DIR / "process_host.Dockerfile", project / "Dockerfile")
    (project / "requirements.txt").write_text("\n".join(PROCESS_HOST_REQUIREMENTS) + "\n", encoding="utf-8")

    service_key = f"process-{spec.name}"
    service: dict[str, Any] = {
        "host": "azure.ai.agent",
        "project": f"./processes/{spec.name}",
        "language": "docker",
        "docker": {"path": "Dockerfile"},
        "uses": [PROJECT_SERVICE, *info.agents],
        "kind": "hosted",
        "name": process_agent_name(org, spec.name),
        "description": spec.description.strip()[:512],
        "protocols": [{"protocol": "invocations", "version": "1.0.0"}],
        "env": {
            # Resolved by Foundry at runtime ($$ keeps the expression away from azd's own substitution).
            "FOUNDRY_PROJECT_ENDPOINT": "$${{project.endpoint}}",
            "HIVEMIND_PROCESS": spec.name,
        },
        "metadata": {
            "managed_by": "hivemind",
            "hm_process": spec.name,
            "hm_owner": spec.owner or "",
            "hm_goal": spec.goal or "",
            "hm_cost_center": spec.budget.cost_center or "",
        },
    }
    service["metadata"] = {k: v for k, v in service["metadata"].items() if v}
    return service_key, service, info.agents


def build(org: Org, out: Path, fetch: Fetcher | None = None, git_sha: str | None = None) -> BuildResult:
    """Write ``out/azure.yaml`` (and the files it references) for the loaded environment."""
    # Regenerate everything except azd's own environment state (.azure), which must survive between builds.
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        if child.name == ".azure":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    fetch = fetch or SourceResolver(org.root)
    services: dict[str, Any] = {PROJECT_SERVICE: project_service(org)}
    builds: list[AgentBuild] = []

    for name, agent in sorted(org.active_agents().items()):
        build_ = compile_agent(org, agent, fetch, git_sha)
        builds.append(build_)
        services[name] = agent_service(org, build_)
        for schedule in agent.schedules:
            services[f"{name}-{schedule.name}"] = _routine(
                name,
                f"HiveMind schedule '{schedule.name}' for {agent.title}",
                schedule.cron,
                schedule.time_zone,
                schedule.enabled,
                {"type": "invoke_agent_responses_api", "agentName": name, "input": schedule.input},
            )
        source = dataset_path(org, agent)
        dataset_file = None
        if source is not None and source.is_file():
            target = out / "evals" / "datasets" / f"{name}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            dataset_file = f"./evals/datasets/{name}.jsonl"
        evals = eval_service(org, agent, name, build_.name, dataset_file)
        if evals:
            services[f"{name}-evals"] = {"host": "azure.ai.eval", "uses": [PROJECT_SERVICE, name], **evals}

    processes: list[str] = []
    for spec in sorted(org.active_processes().values(), key=lambda p: p.name):
        key, service, _agents = _write_process(org, spec, out)
        services[key] = service
        processes.append(spec.name)
        for trigger in spec.triggers:
            services[f"{key}-{trigger.name}"] = _routine(
                key,
                f"HiveMind trigger '{trigger.name}' for the {spec.title} process",
                trigger.cron,
                trigger.time_zone,
                trigger.enabled,
                {"type": "invoke_agent_invocations_api", "agentName": key, "input": {"input": trigger.input}},
            )

    document = {
        "name": f"hivemind-{_slug(org.config.org.name)}-{org.env_name or 'local'}",
        "metadata": {"template": "hivemind@0.3.0"},
        "services": services,
    }
    header = (
        f"# Generated by HiveMind from {org.root.name} for environment '{org.env_name}'"
        f"{f' at {git_sha}' if git_sha else ''}. Do not edit: regenerate with `hivemind build`.\n"
    )
    (out / "azure.yaml").write_text(
        SCHEMA_COMMENT + header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    (out / "hivemind-build.json").write_text(
        json.dumps(
            {
                "org": org.config.org.name,
                "environment": org.env_name,
                "git_sha": git_sha,
                "agents": {
                    b.spec_name: {
                        "name": b.name,
                        "hash": b.content_hash,
                        "autonomy": b.autonomy.value,
                        "model": resolve_model(org, org.agents[b.spec_name]),
                    }
                    for b in builds
                },
                "processes": processes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return BuildResult(out, document, builds, processes)
