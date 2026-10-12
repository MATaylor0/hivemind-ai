from __future__ import annotations

from ..loader import Org
from .base import DeployedAgent, Provider


def provider_for(org: Org) -> Provider:
    env = org.env
    if env.provider == "local":
        from .local import LocalProvider

        return LocalProvider(org.root / ".hivemind" / (org.env_name or "local"))
    if not env.endpoint:
        raise ValueError(f"environment '{org.env_name}' has no Foundry project endpoint")
    from .foundry import FoundryProvider

    return FoundryProvider(env.endpoint)


__all__ = ["DeployedAgent", "Provider", "provider_for"]
