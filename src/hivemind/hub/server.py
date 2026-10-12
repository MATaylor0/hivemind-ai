"""MCP front door for ``HubService`` (streamable HTTP, which Foundry's MCP tool speaks), plus the task runner.

Authentication is pluggable (see ``hub.auth``): Microsoft Entra agent identity tokens in production, a shared
key for development. The calling agent's identity is resolved per request; tools never take a "caller" argument.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..loader import Org
from ..tasks import task_store_for
from ..workspace import workspace_for
from .auth import Authenticator, EntraAuthenticator, KeyAuthenticator, identities_from_deployed
from .memory import FoundryMemoryBackend, InMemoryBackend, MemoryBackend
from .service import Dispatcher, HubError, HubService, audit_log
from .worker import TaskRunner

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
            memory = FoundryMemoryBackend(client, f"{org.env.name_prefix}{org.config.memory.store}")
        else:
            memory = InMemoryBackend()
    if dispatch is None:
        if org.env.provider == "foundry":
            dispatch = foundry_dispatcher(client or _foundry_client(org))
        else:
            from ..providers import provider_for

            dispatch = provider_for(org).invoke
    return HubService(org, memory, dispatch, tasks=task_store_for(org), workspace=workspace_for(org))


def build_authenticator(org: Org, insecure: bool = False) -> Authenticator:
    hub = org.config.hub
    if hub.auth == "key" or insecure or org.env.provider != "foundry":
        key = os.environ.get("HIVEMIND_HUB_KEY")
        if not key and not insecure:
            raise RuntimeError("set HIVEMIND_HUB_KEY (or pass --insecure for local development)")
        return KeyAuthenticator(key)
    tenant = hub.tenant_id or os.environ.get("AZURE_TENANT_ID")
    if not tenant or not hub.audience:
        raise RuntimeError("hub.auth is 'entra': set hub.tenant_id (or AZURE_TENANT_ID) and hub.audience")
    from ..providers import provider_for

    provider = provider_for(org)
    return EntraAuthenticator(tenant, hub.audience, lambda: identities_from_deployed(provider.list_managed()))


def create_server(service: HubService, authenticator: Authenticator) -> MCPServer:
    server = MCPServer(
        name="hivemind",
        instructions="HiveMind hub: delegate to colleagues, escalate, request review, track tasks, save outputs "
        "and use shared memory.",
    )

    def guarded(call: Callable[..., T], ctx: Context, *args: Any) -> T:
        """Return refusals to the calling agent verbatim so it can correct course (e.g. pick an allowed colleague)."""
        try:
            return call(authenticator.identify(ctx.headers or {}), *args)
        except HubError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(description="List the agents in the organisation and your relationship to each.")
    def list_colleagues(ctx: Context) -> list[dict[str, str]]:
        return guarded(service.list_colleagues, ctx)

    @server.tool(
        description="Give a well-scoped task to a direct report or collaborator. Returns the result if it finishes "
        "within wait_seconds, otherwise a task id (the result then arrives as a follow-up task). Pass parent_task "
        "when working on a task, and goal to link the work to an organisational goal."
    )
    def delegate_task(
        to: str,
        task: str,
        ctx: Context,
        context: str = "",
        parent_task: str | None = None,
        goal: str | None = None,
        wait_seconds: int | None = None,
    ) -> dict[str, Any]:
        return guarded(service.delegate_task, ctx, to, task, context, parent_task, goal, wait_seconds)

    @server.tool(description="Escalate an issue to your manager. Returns their response or a task id.")
    def escalate(
        issue: str, ctx: Context, context: str = "", parent_task: str | None = None, wait_seconds: int | None = None
    ) -> dict[str, Any]:
        return guarded(service.escalate, ctx, issue, context, parent_task, wait_seconds)

    @server.tool(description="Ask your reviewer to approve work. Returns APPROVED or CHANGES REQUIRED with reasons.")
    def request_review(
        work: str, ctx: Context, criteria: str = "", parent_task: str | None = None, wait_seconds: int | None = None
    ) -> dict[str, Any]:
        return guarded(service.request_review, ctx, work, criteria, parent_task, wait_seconds)

    @server.tool(description="Get the status and result of a task you requested or were assigned.")
    def check_task(task_id: str, ctx: Context) -> dict[str, Any]:
        return guarded(service.check_task, ctx, task_id)

    @server.tool(description="List your open tasks: work assigned to you and work you are waiting on.")
    def my_tasks(ctx: Context) -> dict[str, list[dict[str, str]]]:
        return guarded(service.my_tasks, ctx)

    @server.tool(
        description="Save a finished document (markdown content) to your team's SharePoint outputs library and get "
        "a link to share. Attaches the link to your current task."
    )
    def save_output(title: str, content: str, ctx: Context, task_id: str | None = None) -> dict[str, str]:
        return guarded(service.save_output, ctx, title, content, task_id)

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

    service = build_service(org)
    server = create_server(service, build_authenticator(org, insecure))

    stop = threading.Event()
    if service.config.workers > 0:
        runner = TaskRunner(service)
        threading.Thread(target=runner.serve, args=(stop,), name="hm-task-runner", daemon=True).start()

    log.info("HiveMind hub serving %d agents on %s:%d", len(service.chart.agents), host, port)
    try:
        # Stateless JSON responses let the hub scale out behind a load balancer (e.g. Azure Container Apps).
        server.run(transport="streamable-http", host=host, port=port, stateless_http=True, json_response=True)
    finally:
        stop.set()
