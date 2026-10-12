"""Microsoft Foundry Agent Service provider (azure-ai-projects >= 2.8).

This is the only module that talks to Foundry. Each ``hivemind apply`` creates a new immutable agent
*version* whose metadata records the HiveMind content hash, owner, role, cost centre and git SHA, so
Foundry's own portal, Control Plane and tracing remain the system of record.
"""

from __future__ import annotations

import copy
from typing import Any

from ..compiler import MANAGED_BY, AgentBuild, MemoryStoreRequirement
from ..schema import M365Channel, Schedule
from .base import DeployedAgent, Provider

ROUTINE_SEPARATOR = "--"


def _require_sdk() -> None:
    try:
        import azure.ai.projects  # noqa: F401
        import azure.identity  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("The Foundry provider needs the 'azure' extra: pip install 'hivemind-ai[azure]'") from exc


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """SDK models are both attribute- and mapping-accessible; tolerate either."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class FoundryProvider(Provider):
    def __init__(self, endpoint: str, credential: Any = None, client: Any = None):
        if client is None:
            _require_sdk()
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            # allow_preview: memory, SharePoint, Fabric and routines are preview features in Foundry today.
            client = AIProjectClient(
                endpoint=endpoint, credential=credential or DefaultAzureCredential(), allow_preview=True
            )
        self.client = client
        self._connection_ids: dict[str, str] = {}

    # ------------------------------------------------------------------ helpers

    def _connection_id(self, name_or_id: str) -> str:
        """Catalog files may name a project connection; Foundry wants its id."""
        if not name_or_id or name_or_id.startswith("/subscriptions/"):
            return name_or_id
        if name_or_id not in self._connection_ids:
            connection = self.client.connections.get(name_or_id)
            self._connection_ids[name_or_id] = _get(connection, "id")
        return self._connection_ids[name_or_id]

    def resolve_connections(self, definition: dict[str, Any]) -> dict[str, Any]:
        resolved = copy.deepcopy(definition)

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "project_connection_id" and isinstance(value, str):
                        node[key] = self._connection_id(value)
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(resolved)
        return resolved

    @staticmethod
    def _to_deployed(details: Any) -> DeployedAgent | None:
        latest = _get(_get(details, "versions"), "latest")
        metadata = dict(_get(latest, "metadata") or {})
        if metadata.get("managed_by") != MANAGED_BY:
            return None
        created = _get(latest, "created_at")
        identity = _get(latest, "instance_identity") or _get(details, "instance_identity")
        return DeployedAgent(
            principal_id=_get(identity, "principal_id"),
            client_id=_get(identity, "client_id"),
            name=_get(details, "name"),
            version=str(_get(latest, "version", "")),
            content_hash=metadata.get("hm_hash", ""),
            spec_name=metadata.get("hm_spec"),
            metadata=metadata,
            model=_get(_get(latest, "definition"), "model"),
            created_at=created.isoformat() if hasattr(created, "isoformat") else created,
        )

    # ------------------------------------------------------------------ Provider API

    def list_managed(self) -> dict[str, DeployedAgent]:
        out: dict[str, DeployedAgent] = {}
        for details in self.client.agents.list():
            deployed = self._to_deployed(details)
            if deployed:
                out[deployed.name] = deployed
        return out

    def deploy(self, build: AgentBuild) -> DeployedAgent:
        from azure.ai.projects.models import PromptAgentDefinition

        definition = PromptAgentDefinition(self.resolve_connections(build.definition))
        version = self.client.agents.create_version(
            agent_name=build.name,
            definition=definition,
            description=build.description,
            metadata=build.metadata,
        )
        return DeployedAgent(
            name=build.name,
            version=str(_get(version, "version", "")),
            content_hash=build.content_hash,
            spec_name=build.spec_name,
            metadata=build.metadata,
            model=build.definition.get("model"),
        )

    def delete(self, name: str) -> None:
        for routine in self._routines_for(name):
            self.client.beta.routines.delete(_get(routine, "name"))
        self.client.agents.delete(name)

    def ensure_memory_store(self, store: MemoryStoreRequirement) -> bool:
        from azure.ai.projects.models import MemoryStoreDefaultDefinition
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self.client.beta.memory_stores.get(store.name)
            return False
        except ResourceNotFoundError:
            self.client.beta.memory_stores.create(
                name=store.name,
                definition=MemoryStoreDefaultDefinition(
                    chat_model=store.chat_model, embedding_model=store.embedding_model
                ),
                description="Private memory managed by HiveMind",
            )
            return True

    def _routines_for(self, agent_name: str) -> list[Any]:
        prefix = f"{agent_name}{ROUTINE_SEPARATOR}"
        return [r for r in self.client.beta.routines.list() if str(_get(r, "name", "")).startswith(prefix)]

    def sync_schedules(self, build: AgentBuild, schedules: list[Schedule]) -> list[str]:
        """Schedules become Foundry routines named ``<agent>--<schedule>`` that invoke the agent."""
        from azure.ai.projects.models import InvokeAgentResponsesApiRoutineAction, ScheduleRoutineTrigger

        changes: list[str] = []
        desired = {f"{build.name}{ROUTINE_SEPARATOR}{s.name}": s for s in schedules}
        for routine in self._routines_for(build.name):
            routine_name = _get(routine, "name")
            if routine_name not in desired:
                self.client.beta.routines.delete(routine_name)
                changes.append(f"routine {routine_name}: removed")
        for routine_name, schedule in desired.items():
            self.client.beta.routines.create_or_update(
                routine_name,
                description=f"HiveMind schedule '{schedule.name}' for {build.name}",
                enabled=schedule.enabled,
                triggers={
                    "schedule": ScheduleRoutineTrigger(cron_expression=schedule.cron, time_zone=schedule.time_zone)
                },
                action=InvokeAgentResponsesApiRoutineAction(agent_name=build.name, input=schedule.input),
            )
            changes.append(f"routine {routine_name}: applied ({schedule.cron} {schedule.time_zone})")
        return changes

    def publish_m365(self, name: str, channel: M365Channel, description: str) -> str:
        scope = {"personal": "Personal", "shared": "Shared", "tenant": "Tenant"}[channel.scope]
        result = self.client.agents.publish_to_microsoft365(
            name,
            publish_scope=scope,
            agent_display_name=channel.display_name or name,
            short_description=(channel.short_description or description)[:80],
            full_description=description,
        )
        return f"published {name} to Microsoft 365 (scope={scope}, teams_app_id={_get(result, 'teams_app_id')})"

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Pause or resume an agent in Foundry without deleting it (e.g. over budget, under investigation)."""
        (self.client.agents.enable if enabled else self.client.agents.disable)(name)

    def invoke(self, name: str, message: str) -> str:
        openai = self.client.get_openai_client()
        response = openai.responses.create(
            input=message, extra_body={"agent_reference": {"name": name, "type": "agent_reference"}}
        )
        return response.output_text
