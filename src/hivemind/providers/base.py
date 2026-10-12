"""Provider interface. A provider owns the side effects; everything upstream is pure."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..compiler import AgentBuild, MemoryStoreRequirement
from ..schema import M365Channel, Schedule


@dataclass
class DeployedAgent:
    name: str
    version: str
    content_hash: str
    spec_name: str | None
    metadata: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    created_at: str | None = None


class Provider(ABC):
    @abstractmethod
    def list_managed(self) -> dict[str, DeployedAgent]:
        """Every agent in the target created by HiveMind, keyed by provider name."""

    @abstractmethod
    def deploy(self, build: AgentBuild) -> DeployedAgent:
        """Create a new immutable version of the agent."""

    @abstractmethod
    def delete(self, name: str) -> None:
        """Remove an agent (all versions)."""

    def ensure_memory_store(self, store: MemoryStoreRequirement) -> bool:
        """Create the memory store if missing. Returns True if it was created."""
        return False

    def sync_schedules(self, build: AgentBuild, schedules: list[Schedule]) -> list[str]:
        """Make the agent's schedules match ``schedules``. Returns human-readable changes."""
        return []

    def publish_m365(self, name: str, channel: M365Channel, description: str) -> str:
        raise NotImplementedError(f"{type(self).__name__} cannot publish to Microsoft 365")

    def invoke(self, name: str, message: str) -> str:
        raise NotImplementedError(f"{type(self).__name__} cannot invoke agents")
