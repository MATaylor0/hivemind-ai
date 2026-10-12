"""Load an organisation directory into validated models.

Layout (every directory is optional except ``hivemind.yaml`` and ``agents/``)::

    <org>/
      hivemind.yaml              # OrgConfig
      catalog/tools/*.yaml       # ToolDef (one per file, or a list)
      catalog/knowledge/*.yaml   # KnowledgeDef
      roles/*.yaml               # RoleDef
      agents/**/*.yaml           # AgentSpec (sub-folders per team are fine)
      processes/*.yaml           # ProcessSpec, next to its Agent Framework workflow (*.workflow.yaml)
      evals/**                   # golden datasets (JSONL) referenced by agents' `evals.dataset`
      instructions/**            # markdown referenced by `file:` sources

String values may reference ``${env:NAME}`` (process environment) and ``${var:NAME}`` (the selected
environment's ``variables``), so one spec deploys to dev, test and prod.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import AgentSpec, Environment, KnowledgeDef, OrgConfig, ProcessSpec, RoleDef, ToolDef

_PLACEHOLDER = re.compile(r"\$\{(env|var):([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class LoadError(Exception):
    """Raised when the org directory cannot be loaded. Message lists every problem found."""


@dataclass
class Org:
    root: Path
    config: OrgConfig
    tools: dict[str, ToolDef] = field(default_factory=dict)
    knowledge: dict[str, KnowledgeDef] = field(default_factory=dict)
    roles: dict[str, RoleDef] = field(default_factory=dict)
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    processes: dict[str, ProcessSpec] = field(default_factory=dict)
    env_name: str | None = None
    sources: dict[str, Path] = field(default_factory=dict)  # "<Model>:<id>" -> file it came from

    @property
    def env(self) -> Environment:
        if self.env_name is None:
            return Environment(provider="local")
        return self.config.environments[self.env_name]

    def role_of(self, agent: AgentSpec | str) -> RoleDef:
        spec = self.agents[agent] if isinstance(agent, str) else agent
        return self.roles[spec.role]

    def active_agents(self) -> dict[str, AgentSpec]:
        disabled = set(self.env.disabled_agents)
        return {n: a for n, a in self.agents.items() if a.enabled and n not in disabled}

    def active_processes(self) -> dict[str, ProcessSpec]:
        return {n: p for n, p in self.processes.items() if p.enabled}


def substitute(value: Any, variables: dict[str, str], strict: bool, problems: list[str], where: str) -> Any:
    """Recursively replace ``${env:X}`` / ``${var:X}`` (with optional ``:-default``) in strings."""
    if isinstance(value, dict):
        return {k: substitute(v, variables, strict, problems, where) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables, strict, problems, where) for v in value]
    if not isinstance(value, str):
        return value

    def repl(match: re.Match[str]) -> str:
        kind, name, default = match.group(1), match.group(2), match.group(3)
        found = os.environ.get(name) if kind == "env" else variables.get(name)
        if found is not None:
            return found
        if default is not None:
            return default
        if strict:
            problems.append(f"{where}: unresolved ${{{kind}:{name}}}")
        return match.group(0)

    return _PLACEHOLDER.sub(repl, value)


def _read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise LoadError(f"{path}: invalid YAML: {exc}") from exc


def _yaml_files(directory: Path) -> Iterable[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file())


def _fmt_validation(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}:"]
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_org(root: str | Path, env: str | None = None) -> Org:
    """Load and validate everything under ``root``. When ``env`` is given, placeholders must resolve."""
    root = Path(root).resolve()
    config_path = root / "hivemind.yaml"
    if not config_path.is_file():
        raise LoadError(f"{config_path} not found - is '{root}' a HiveMind org directory?")

    problems: list[str] = []
    strict = env is not None

    raw_config = _read_yaml(config_path) or {}
    # Environments are resolved against the process environment only.
    raw_config = substitute(raw_config, {}, False, problems, str(config_path))
    try:
        config = OrgConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise LoadError(_fmt_validation(config_path, exc)) from exc

    if env is not None and env not in config.environments:
        raise LoadError(f"unknown environment '{env}'. Defined: {', '.join(config.environments) or 'none'}")

    variables = config.environments[env].variables if env else {}
    if env:
        # Second pass so ${env:...} left unresolved in hivemind.yaml are reported for the chosen env.
        substitute(config.environments[env].model_dump(), {}, True, problems, f"{config_path} (env {env})")

    org = Org(root=root, config=config, env_name=env)

    def load_many(directory: str, model: type, target: dict, key: str) -> None:
        for path in _yaml_files(root / directory):
            data = _read_yaml(path)
            if data is None:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("kind") == "Workflow":
                    continue  # Agent Framework workflow files live next to their process specs
                item = substitute(item, variables, strict, problems, str(path))
                try:
                    obj = model.model_validate(item)
                except ValidationError as exc:
                    problems.append(_fmt_validation(path, exc))
                    continue
                ident = getattr(obj, key)
                source_key = f"{model.__name__}:{ident}"
                if ident in target:
                    problems.append(f"{path}: duplicate {model.__name__} '{ident}' (also in {org.sources[source_key]})")
                    continue
                target[ident] = obj
                org.sources[source_key] = path

    load_many("catalog/tools", ToolDef, org.tools, "id")
    load_many("catalog/knowledge", KnowledgeDef, org.knowledge, "id")
    load_many("roles", RoleDef, org.roles, "id")
    load_many("agents", AgentSpec, org.agents, "name")
    load_many("processes", ProcessSpec, org.processes, "name")

    clash = set(org.tools) & set(org.knowledge)
    if clash:
        problems.append(f"ids used for both a tool and a knowledge source: {', '.join(sorted(clash))}")

    if problems:
        raise LoadError("\n".join(problems))
    return org
