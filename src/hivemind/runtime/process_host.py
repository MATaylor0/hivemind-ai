"""HiveMind process host: runs one Agent Framework declarative workflow as a Foundry hosted agent.

Generated into each process's deployment project by `hivemind build` (as main.py, next to workflow.yaml).
Protocol: invocations (JSON in, JSON out).

  Start:   {"input": "Process the invoices received this morning"}
  Resume:  {"session": "<id>", "responses": {"<request id>": "approved"}}

  Reply:   {"session": "<id>", "status": "completed" | "awaiting_input",
            "outputs": ["..."], "requests": [{"id": "...", "prompt": "Approve payment of invoice 42?"}]}

Each workflow step that invokes an agent calls the Foundry prompt agent of that name (deployed by the same
azd project) with this container's identity. Human steps (RequestExternalInput / Question) pause the run and
return the request; the caller (Teams, Power Automate, a person in the Foundry playground) resumes it.

Paused runs are held in memory for the session, so approvals must arrive within the hosted agent's session
lifetime. Durable checkpoints are on the HiveMind roadmap.
"""

from __future__ import annotations

import os
import time
import uuid
import warnings
from pathlib import Path
from typing import Any

import yaml
from agent_framework_declarative import ExternalInputResponse, WorkflowFactory
from agent_framework_foundry import FoundryAgent
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.identity.aio import DefaultAzureCredential
from starlette.requests import Request
from starlette.responses import JSONResponse

WORKFLOW = yaml.safe_load((Path(__file__).parent / "workflow.yaml").read_text(encoding="utf-8"))
ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
SESSION_TTL_SECONDS = int(os.environ.get("HIVEMIND_SESSION_TTL_SECONDS", "86400"))


def _agent_names(actions: Any) -> set[str]:
    names: set[str] = set()
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict):
            continue
        if action.get("kind") == "InvokeAzureAgent":
            agent = action.get("agent")
            name = agent.get("name") if isinstance(agent, dict) else agent or action.get("agentName")
            if name:
                names.add(str(name))
        for key in ("actions", "elseActions"):
            names |= _agent_names(action.get(key))
        for condition in action.get("conditions", []) or []:
            names |= _agent_names(condition.get("actions") if isinstance(condition, dict) else None)
    return names


credential = DefaultAzureCredential()
AGENTS = {
    name: FoundryAgent(project_endpoint=ENDPOINT, agent_name=name, credential=credential)
    for name in sorted(_agent_names(WORKFLOW.get("trigger", {}).get("actions")))
}
sessions: dict[str, tuple[float, Any]] = {}  # session id -> (last used, workflow instance)
app = InvocationAgentServerHost()


def _new_workflow() -> Any:
    with warnings.catch_warnings():  # Agent Framework marks declarative agents as experimental
        warnings.simplefilter("ignore")
        return WorkflowFactory(agents=AGENTS).create_workflow_from_definition(WORKFLOW)


def _reply(session: str, result: Any) -> dict[str, Any]:
    requests = [
        {"id": r.request_id, "prompt": str(getattr(r.data, "message", "") or r.data)}
        for r in result.get_request_info_events()
    ]
    return {
        "session": session,
        "status": "awaiting_input" if requests else "completed",
        "outputs": [str(o) for o in result.get_outputs()],
        "requests": requests,
    }


@app.invoke_handler
async def handle(request: Request) -> JSONResponse:
    body = await request.json()
    now = time.monotonic()
    for key in [k for k, (used, _) in sessions.items() if now - used > SESSION_TTL_SECONDS]:
        sessions.pop(key, None)

    if body.get("responses"):
        session = str(body.get("session", ""))
        if session not in sessions:
            return JSONResponse({"error": f"unknown or expired session '{session}'"}, status_code=404)
        workflow = sessions[session][1]
        responses = {
            request_id: ExternalInputResponse(user_input=str(answer), value=answer)
            for request_id, answer in body["responses"].items()
        }
        result = await workflow.run(responses=responses)
    else:
        session = str(body.get("session") or uuid.uuid4())
        workflow = _new_workflow()
        result = await workflow.run(str(body.get("input", "")))

    reply = _reply(session, result)
    if reply["status"] == "awaiting_input":
        sessions[session] = (now, workflow)
    else:
        sessions.pop(session, None)
    return JSONResponse(reply)


if __name__ == "__main__":
    app.run()
