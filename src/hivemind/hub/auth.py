"""Who is calling the hub?

``EntraAuthenticator`` (production) relies on the identities Foundry already provisions: when a hub tool uses an
*agentic identity* project connection, Foundry calls the hub with a Microsoft Entra token for the agent's identity.

* A **published** agent has its own Entra Agent ID, so the token alone identifies it.
* **Unpublished** agents share the project's default agent identity. The token proves the caller is an agent of
  this project; the ``x-hivemind-agent`` header (written into the definition at deploy time, not chosen by the
  model) says which one, and must name an agent that uses that identity.

The identity map (principal id -> agent names) is read from Foundry's agent records and refreshed periodically.

``KeyAuthenticator`` (development) accepts a shared key plus the header.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from ..compiler import HUB_AGENT_HEADER
from .service import HubError

KEY_HEADER = "x-hivemind-key"
IdentityMap = dict[str, set[str]]  # Entra principal (object) id -> agent spec names


class Authenticator(Protocol):
    def identify(self, headers: Mapping[str, str]) -> str: ...


def _lower(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


class KeyAuthenticator:
    def __init__(self, api_key: str | None):
        self.api_key = api_key

    def identify(self, headers: Mapping[str, str]) -> str:
        h = _lower(headers)
        if self.api_key is not None and not hmac.compare_digest(h.get(KEY_HEADER, ""), self.api_key):
            raise HubError("unauthorised: missing or invalid hub key")
        agent = h.get(HUB_AGENT_HEADER, "")
        if not agent:
            raise HubError(f"missing {HUB_AGENT_HEADER} header")
        return agent


class EntraAuthenticator:
    def __init__(
        self,
        tenant_id: str,
        audience: str,
        identities: Callable[[], IdentityMap],
        signing_key: Callable[[str], Any] | None = None,
        refresh_seconds: int = 600,
    ):
        import jwt

        self._jwt = jwt
        self.tenant_id = tenant_id
        self.audience = audience
        self.issuers = [
            f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            f"https://sts.windows.net/{tenant_id}/",
        ]
        if signing_key is None:
            jwks = jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")
            signing_key = lambda token: jwks.get_signing_key_from_jwt(token).key  # noqa: E731
        self._signing_key = signing_key
        self._load_identities = identities
        self._identities: IdentityMap = {}
        self._loaded_at = 0.0
        self._refresh = refresh_seconds
        self._lock = threading.Lock()

    def identity_map(self, force: bool = False) -> IdentityMap:
        with self._lock:
            if force or time.monotonic() - self._loaded_at > self._refresh:
                self._identities = self._load_identities()
                self._loaded_at = time.monotonic()
            return self._identities

    def identify(self, headers: Mapping[str, str]) -> str:
        h = _lower(headers)
        auth = h.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HubError("unauthorised: missing bearer token")
        token = auth[7:].strip()
        try:
            claims = self._jwt.decode(
                token,
                self._signing_key(token),
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuers,
                options={"require": ["exp", "iss", "aud"]},
            )
        except self._jwt.PyJWTError as exc:
            raise HubError(f"unauthorised: invalid token ({exc})") from exc
        if claims.get("tid") not in (None, self.tenant_id):
            raise HubError("unauthorised: token from another tenant")

        principal = claims.get("oid") or claims.get("sub")
        agents = self.identity_map().get(principal) or self.identity_map(force=True).get(principal)
        if not agents:
            raise HubError("unauthorised: this identity does not belong to a HiveMind agent")
        named = h.get(HUB_AGENT_HEADER, "")
        if len(agents) == 1:
            (only,) = agents
            if named and named != only:
                raise HubError(f"unauthorised: token belongs to '{only}', not '{named}'")
            return only
        if named in agents:
            return named
        raise HubError(f"unauthorised: shared project identity requires a valid {HUB_AGENT_HEADER} header")


def identities_from_deployed(deployed: Mapping[str, Any]) -> IdentityMap:
    """Build the identity map from ``Provider.list_managed()`` output."""
    out: IdentityMap = {}
    for agent in deployed.values():
        if agent.principal_id and agent.spec_name:
            out.setdefault(agent.principal_id, set()).add(agent.spec_name)
    return out
