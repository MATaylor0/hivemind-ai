"""Compile an ``AgentSpec`` into a deployable, provider-ready build.

The output (``AgentBuild``) is plain JSON in the Foundry Agent Service wire format, so it can be
diffed, hashed, reviewed in a pull request and sent to Foundry without further translation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import yaml

from .hierarchy import OrgChart
from .instructions import Fetcher, SourceResolver, compose
from .loader import Org
from .schema import AgentSpec, Schedule, ToolDef

MANAGED_BY = "hivemind"
HUB_SERVER_LABEL = "hivemind"
HUB_AGENT_HEADER = "x-hivemind-agent"
USER_SCOPE = "{{$userId}}"  # Foundry memory placeholder: partitions memories per end user.


@dataclass
class MemoryStoreRequirement:
    name: str
    chat_model: str
    embedding_model: str


@dataclass
class AgentBuild:
    spec_name: str
    name: str  # name in Foundry (environment prefix applied)
    description: str
    definition: dict[str, Any]
    metadata: dict[str, str]
    tool_ids: list[str]
    instruction_sources: list[str]
    schedules: list[Schedule] = field(default_factory=list)
    memory_store: MemoryStoreRequirement | None = None
    content_hash: str = ""

    @property
    def instructions(self) -> str:
        return self.definition.get("instructions", "")


class CompileError(Exception):
    pass


def foundry_name(org: Org, spec_name: str) -> str:
    return f"{org.env.name_prefix}{spec_name}"


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def effective_tool_ids(org: Org, agent: AgentSpec) -> list[str]:
    """Tools and knowledge, inherited org -> role -> team -> agent, in that order, without duplicates."""
    role = org.roles[agent.role]
    team = org.config.teams.get(agent.team) if agent.team else None
    ids = [
        *org.config.defaults.knowledge,
        *org.config.defaults.tools,
        *role.knowledge,
        *role.tools,
        *(team.knowledge if team else []),
        *(team.tools if team else []),
        *agent.knowledge,
        *agent.tools,
    ]
    return _dedupe(ids)


def resolve_tool(org: Org, tool_id: str) -> ToolDef:
    if tool_id in org.tools:
        return org.tools[tool_id]
    if tool_id in org.knowledge:
        return org.knowledge[tool_id].as_tool()
    raise CompileError(f"unknown tool or knowledge source '{tool_id}'")


def _connections(connection: str) -> list[dict[str, str]]:
    return [{"project_connection_id": connection}]


def _approval(value: str | dict[str, list[str]]) -> Any:
    """``always``/``never``, or per-tool lists: ``{always: [post_payment], never: [get_invoice]}``."""
    if isinstance(value, str):
        return value
    return {mode: {"tool_names": names} for mode, names in value.items()}


def tool_to_foundry(org: Org, tool: ToolDef) -> dict[str, Any]:
    """Translate a catalog entry into a Foundry tool definition."""
    kind = tool.kind
    if kind == "mcp":
        out: dict[str, Any] = {
            "type": "mcp",
            "server_label": tool.server_label or tool.id.replace("-", "_"),
            "server_url": tool.server_url,
            "require_approval": _approval(tool.require_approval),
        }
        if tool.description:
            out["server_description"] = tool.description
        if tool.allowed_tools:
            out["allowed_tools"] = tool.allowed_tools
        if tool.headers:
            out["headers"] = tool.headers
        if tool.connection:
            out["project_connection_id"] = tool.connection
        return out
    if kind == "openapi":
        assert tool.openapi_spec is not None
        spec_path = (org.root / tool.openapi_spec).resolve()
        if not spec_path.is_file():
            raise CompileError(f"tool '{tool.id}': OpenAPI spec not found at {tool.openapi_spec}")
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        return {
            "type": "openapi",
            "openapi": {
                "name": tool.id.replace("-", "_"),
                "description": tool.description,
                "spec": spec,
                "auth": tool.openapi_auth,
            },
        }
    if kind == "azure_ai_search":
        index: dict[str, Any] = {"project_connection_id": tool.connection, "index_name": tool.index_name}
        if tool.query_type:
            index["query_type"] = tool.query_type
        if tool.top_k:
            index["top_k"] = tool.top_k
        return {"type": "azure_ai_search", "azure_ai_search": {"indexes": [index]}}
    if kind == "sharepoint":
        return {
            "type": "sharepoint_grounding_preview",
            "sharepoint_grounding_preview": {"project_connections": _connections(tool.connection or "")},
        }
    if kind == "fabric_data_agent":
        return {
            "type": "fabric_dataagent_preview",
            "fabric_dataagent_preview": {"project_connections": _connections(tool.connection or "")},
        }
    if kind == "fabric_iq":
        return {"type": "fabric_iq_preview", "project_connection_id": tool.connection}
    if kind == "work_iq":
        return {"type": "work_iq_preview", "project_connection_id": tool.connection}
    if kind == "code_interpreter":
        return {"type": "code_interpreter", "container": {"type": "auto"}}
    if kind == "web_search":
        return {"type": "web_search"}
    if kind == "bing_grounding":
        return {
            "type": "bing_grounding",
            "bing_grounding": {"search_configurations": [{"project_connection_id": tool.connection}]},
        }
    if kind == "a2a":
        out = {"type": "a2a_preview", "base_url": tool.base_url}
        if tool.connection:
            out["project_connection_id"] = tool.connection
        return out
    if kind == "raw":
        assert tool.definition is not None
        return dict(tool.definition)
    raise CompileError(f"tool '{tool.id}': unsupported kind '{kind}'")  # pragma: no cover


def hub_tool(org: Org, agent: AgentSpec) -> dict[str, Any] | None:
    hub = org.config.hub
    if not hub.enabled:
        return None
    url = org.env.hub_url or hub.url
    out: dict[str, Any] = {
        "type": "mcp",
        "server_label": HUB_SERVER_LABEL,
        "server_url": url or "${HUB_URL_NOT_CONFIGURED}",
        "server_description": "HiveMind hub: delegation, escalation, review and shared organisational memory.",
        "require_approval": "never",
        # Identifies the calling agent to the hub. Set by the deployer, not the model; the hub authenticates
        # the caller with the connection's key (see docs/architecture.md, "Trust boundaries").
        "headers": {HUB_AGENT_HEADER: agent.name},
    }
    if hub.connection:
        out["project_connection_id"] = hub.connection
    return out


def shared_memory_store(org: Org) -> MemoryStoreRequirement | None:
    """The store behind the hub's shared scopes (org, team, custom), when Foundry memory is used."""
    mem = org.config.memory
    if not org.config.hub.enabled or mem.backend != "foundry":
        return None
    return MemoryStoreRequirement(
        name=f"{org.env.name_prefix}{mem.store}",
        chat_model=mem.chat_model or org.env.model_overrides.get(org.config.defaults.model, org.config.defaults.model),
        embedding_model=mem.embedding_model or "text-embedding-3-small",
    )


def resolve_model(org: Org, agent: AgentSpec) -> str:
    role = org.roles[agent.role]
    model = agent.model or role.model or org.config.defaults.model
    return org.env.model_overrides.get(model, model)


def _temperature(org: Org, agent: AgentSpec) -> float | None:
    role = org.roles[agent.role]
    for value in (agent.temperature, role.temperature, org.config.defaults.temperature):
        if value is not None:
            return value
    return None


def _metadata(org: Org, agent: AgentSpec, content_hash: str, git_sha: str | None) -> dict[str, str]:
    """Foundry allows 16 metadata pairs (values <= 512 chars). These make the fleet queryable."""
    chart = OrgChart(org.active_agents())
    meta = {
        "managed_by": MANAGED_BY,
        "hm_spec": agent.name,
        "hm_hash": content_hash,
        "hm_role": agent.role,
        "hm_tier": org.roles[agent.role].tier.value,
        "hm_team": agent.team or "",
        "hm_owner": agent.owner or "",
        "hm_reports_to": agent.reports_to or "",
        "hm_reviewed_by": agent.reviewed_by or "",
        "hm_depth": str(chart.depth(agent.name)) if agent.name in chart.agents else "0",
        "hm_cost_center": agent.budget.cost_center or "",
        "hm_budget_usd": "" if agent.budget.monthly_usd is None else f"{agent.budget.monthly_usd:g}",
        "hm_git_sha": git_sha or "",
    }
    for key, value in agent.tags.items():
        if len(meta) >= 16:
            break
        meta.setdefault(f"tag_{key}"[:64], value[:512])
    return {k: v for k, v in meta.items() if v != ""}


def _hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compile_agent(
    org: Org, agent: AgentSpec | str, fetch: Fetcher | None = None, git_sha: str | None = None
) -> AgentBuild:
    spec = org.agents[agent] if isinstance(agent, str) else agent
    fetch = fetch or SourceResolver(org.root)

    tool_ids = effective_tool_ids(org, spec)
    tool_defs = [resolve_tool(org, t) for t in tool_ids]
    tools = [tool_to_foundry(org, t) for t in tool_defs]

    hub = hub_tool(org, spec)
    if hub:
        tools.append(hub)

    memory_store: MemoryStoreRequirement | None = None
    if spec.memory.private:
        mem = org.config.memory
        store_name = f"{foundry_name(org, spec.name)}-memory"
        memory_store = MemoryStoreRequirement(
            name=store_name,
            chat_model=mem.chat_model or resolve_model(org, spec),
            embedding_model=mem.embedding_model or "text-embedding-3-small",
        )
        tools.append(
            {
                "type": "memory_search_preview",
                "memory_store_name": store_name,
                "scope": USER_SCOPE if spec.memory.private_scope == "user" else spec.name,
            }
        )

    composed = compose(org, spec, tool_defs, fetch)

    definition: dict[str, Any] = {
        "kind": "prompt",
        "model": resolve_model(org, spec),
        "instructions": composed.text,
        "tools": tools,
    }
    temperature = _temperature(org, spec)
    if temperature is not None:
        definition["temperature"] = temperature

    description = spec.description.strip()[:512]
    # Schedules are part of the hash so a schedule change produces a new, auditable agent version.
    content_hash = _hash(
        {
            "definition": definition,
            "description": description,
            "schedules": [s.model_dump() for s in spec.schedules],
        }
    )

    return AgentBuild(
        spec_name=spec.name,
        name=foundry_name(org, spec.name),
        description=description,
        definition=definition,
        metadata=_metadata(org, spec, content_hash, git_sha),
        tool_ids=tool_ids,
        instruction_sources=composed.sources,
        schedules=list(spec.schedules),
        memory_store=memory_store,
        content_hash=content_hash,
    )


def compile_org(org: Org, fetch: Fetcher | None = None, git_sha: str | None = None) -> list[AgentBuild]:
    fetch = fetch or SourceResolver(org.root)
    return [compile_agent(org, a, fetch, git_sha) for a in org.active_agents().values()]
