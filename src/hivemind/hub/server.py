"""MCP front door for ``HubService`` (streamable HTTP, which Foundry's MCP tool speaks).

Authentication
--------------
Foundry attaches the hub's project connection (a "custom keys" connection holding ``x-hivemind-key``)
to every hub tool call; the hub rejects calls without the right key. The calling agent is identified
by the ``x-hivemind-agent`` header that HiveMind writes into each agent definition at deploy time.
Only principals who can edit agent definitions in the Foundry project can change that header, so the
trust boundary is the project itself. See docs/architecture.md ("Trust boundaries") for hardening with
Microsoft Entra Agent ID.
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..compiler import HUB_AGENT_HEADER
from ..loader import Org
from .memory import FoundryMemoryBackend, InMemoryBackend, MemoryBackend
from .service import Dispatcher, HubError, HubService, audit_log

KEY_HEADER = "x-hivemind-key"
T = TypeVar("T")
log = logging.getLogger("hivemind.hub")


def _foundry_client(org: Org) -> Any:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    if not org.env.endpoint:
        raise RuntimeError(f"environment '{org.env_name}' has no Foundry endpoint")
    return AIProjectClient(endpoint=org.env.endpoint, credential=DefaultAzureCredential(), allow_preview=True)


def foundry_dispatcher(client: Any) -> Dispatcher:
    openai = client.get_openai_client()

    def dispatch(agent_name: str, message: str) -> str:
        response = openai.responses.create(
            input=message, extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}}
        )
        return response.output_text

    return dispatch


def build_service(org: Org, memory: MemoryBackend | None = None, dispatch: Dispatcher | None = None) -> HubService:
    client = None
    if memory is None:
        if org.config.memory.backend == "foundry" and org.env.provider == "foundry":
            client = _foundry_client(org)
            memory = FoundryMemoryBackend(
                client, f"{org.env.name_prefix}{org.config.memory.store}"
            )  # see shared_memory_store
        else:
            memory = InMemoryBackend()
    if dispatch is None:
        if org.env.provider != "foundry":
            raise RuntimeError("a dispatcher is required when the environment is not a Foundry environment")
        dispatch = foundry_dispatcher(client or _foundry_client(org))
    return HubService(org, memory, dispatch)


def create_server(service: HubService, api_key: str | None) -> MCPServer:
    server = MCPServer(
        name="hivemind",
        instructions="HiveMind hub: delegate to colleagues, escalate, request review and use shared memory.",
    )

    def guarded(call: Callable[..., T], ctx: Context, *args: Any) -> T:
        """Return refusals to the calling agent verbatim so it can correct course (e.g. pick an allowed colleague)."""
        try:
            return call(caller(ctx), *args)
        except HubError as exc:
            raise ToolError(str(exc)) from exc

    def caller(ctx: Context) -> str:
        headers = {k.lower(): v for k, v in (ctx.headers or {}).items()}
        if api_key is not None and not hmac.compare_digest(headers.get(KEY_HEADER, ""), api_key):
            raise HubError("unauthorised: missing or invalid hub key")
        agent = headers.get(HUB_AGENT_HEADER, "")
        if not agent:
            raise HubError(f"missing {HUB_AGENT_HEADER} header")
        return agent

    @server.tool(description="List the agents in the organisation and your relationship to each.")
    def list_colleagues(ctx: Context) -> list[dict[str, str]]:
        return guarded(service.list_colleagues, ctx)

    @server.tool(description="Hand a well-scoped task to a direct report or collaborator and return their answer.")
    def delegate_task(to: str, task: str, ctx: Context, context: str = "") -> str:
        return guarded(service.delegate_task, ctx, to, task, context)

    @server.tool(description="Escalate an issue to your manager and return their response.")
    def escalate(issue: str, ctx: Context, context: str = "") -> str:
        return guarded(service.escalate, ctx, issue, context)

    @server.tool(description="Ask your reviewer to approve work. Returns APPROVED or CHANGES REQUIRED with reasons.")
    def request_review(work: str, ctx: Context, criteria: str = "") -> str:
        return guarded(service.request_review, ctx, work, criteria)

    @server.tool(description="Store a durable fact, decision or outcome in a shared memory scope.")
    def remember(scope: str, content: str, ctx: Context) -> dict[str, str]:
        return guarded(service.remember, ctx, scope, content)

    @server.tool(description="Search a shared memory scope for relevant facts and decisions.")
    def recall(scope: str, query: str, ctx: Context, limit: int = 5) -> list[dict[str, str]]:
        return guarded(service.recall, ctx, scope, query, limit)

    return server


def serve(org: Org, host: str = "0.0.0.0", port: int = 8080, insecure: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Audit records are one JSON object per line on stdout, independent of the MCP server's log formatting,
    # so Log Analytics can parse them (see observability/hub-activity.kql).
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    audit_log.addHandler(handler)
    audit_log.setLevel(logging.INFO)
    audit_log.propagate = False
    api_key = os.environ.get("HIVEMIND_HUB_KEY")
    if not api_key and not insecure:
        raise RuntimeError("set HIVEMIND_HUB_KEY (or pass --insecure for local development)")
    service = build_service(org)
    server = create_server(service, api_key)
    log.info("HiveMind hub serving %d agents on %s:%d", len(service.chart.agents), host, port)
    # Stateless JSON responses let the hub scale out behind a load balancer (e.g. Azure Container Apps).
    server.run(transport="streamable-http", host=host, port=port, stateless_http=True, json_response=True)
