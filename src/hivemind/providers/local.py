"""A file-backed provider. Useful for trying HiveMind without Azure, for tests and for CI dry runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..compiler import AgentBuild, MemoryStoreRequirement
from ..schema import M365Channel, Schedule
from .base import DeployedAgent, Provider


class LocalProvider(Provider):
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "state.json"

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"agents": {}, "memory_stores": [], "schedules": {}, "publications": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, state: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def list_managed(self) -> dict[str, DeployedAgent]:
        out = {}
        for name, versions in self._load()["agents"].items():
            latest = versions[-1]
            out[name] = DeployedAgent(
                name=name,
                version=str(len(versions)),
                content_hash=latest["metadata"].get("hm_hash", ""),
                spec_name=latest["metadata"].get("hm_spec"),
                metadata=latest["metadata"],
                model=latest["definition"].get("model"),
                created_at=latest["created_at"],
            )
        return out

    def deploy(self, build: AgentBuild) -> DeployedAgent:
        state = self._load()
        versions = state["agents"].setdefault(build.name, [])
        versions.append(
            {
                "description": build.description,
                "definition": build.definition,
                "metadata": build.metadata,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        self._save(state)
        return self.list_managed()[build.name]

    def delete(self, name: str) -> None:
        state = self._load()
        state["agents"].pop(name, None)
        state["schedules"].pop(name, None)
        self._save(state)

    def ensure_memory_store(self, store: MemoryStoreRequirement) -> bool:
        state = self._load()
        if store.name in state["memory_stores"]:
            return False
        state["memory_stores"].append(store.name)
        self._save(state)
        return True

    def sync_schedules(self, build: AgentBuild, schedules: list[Schedule]) -> list[str]:
        state = self._load()
        before = state["schedules"].get(build.name, {})
        after = {s.name: s.model_dump() for s in schedules}
        changes = [f"schedule {n}: removed" for n in before if n not in after]
        changes += [
            f"schedule {n}: {'updated' if n in before else 'created'}" for n, v in after.items() if before.get(n) != v
        ]
        state["schedules"][build.name] = after
        self._save(state)
        return changes

    def publish_m365(self, name: str, channel: M365Channel, description: str) -> str:
        state = self._load()
        state["publications"][name] = channel.model_dump()
        self._save(state)
        return f"recorded local publication of {name} (scope={channel.scope})"

    def set_enabled(self, name: str, enabled: bool) -> None:
        state = self._load()
        if name not in state["agents"]:
            raise KeyError(f"agent '{name}' is not deployed")
        paused = set(state.setdefault("paused", []))
        (paused.discard if enabled else paused.add)(name)
        state["paused"] = sorted(paused)
        self._save(state)

    def invoke(self, name: str, message: str) -> str:
        agents = self._load()["agents"]
        if name not in agents:
            raise KeyError(f"agent '{name}' is not deployed")
        if name in self._load().get("paused", []):
            raise RuntimeError(f"agent '{name}' is paused")
        return f"[local:{name}] received: {message}"
