"""The HiveMind hub: governed agent-to-agent collaboration and shared memory.

Every Foundry agent built by HiveMind gets the hub as an MCP tool. The hub knows the org chart (it loads
the same org directory as the CLI), so it enforces the same rules the agents were told about:

* ``delegate_task``  - only to direct reports and declared collaborators.
* ``escalate``       - only to your manager.
* ``request_review`` - only to your reviewer.
* ``remember/recall``- only in memory scopes your role, team and opt-ins allow.

Each call is audited, and delegation depth is bounded to stop agents bouncing work around forever.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..access import memory_access
from ..compiler import foundry_name
from ..hierarchy import OrgChart
from ..loader import Org
from .memory import MemoryBackend

audit_log = logging.getLogger("hivemind.hub.audit")

# Runs an agent (by Foundry name) with a message and returns its text answer.
Dispatcher = Callable[[str, str], str]


class HubError(Exception):
    """Raised for calls the org chart does not allow. The message is returned to the calling agent."""


@dataclass
class _Run:
    agent: str
    depth: int


class HubService:
    def __init__(self, org: Org, memory: MemoryBackend, dispatch: Dispatcher):
        self.org = org
        self.memory = memory
        self.dispatch = dispatch
        self.chart = OrgChart(org.active_agents())
        self.max_depth = org.config.hub.max_delegation_depth
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers

    def _agent(self, caller: str) -> None:
        if caller not in self.chart.agents:
            raise HubError(f"unknown or disabled agent '{caller}'")

    def _audit(self, caller: str, action: str, **fields: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "caller": caller, "action": action, **fields}
        audit_log.info(json.dumps(record, default=str))

    def _current_depth(self, agent: str) -> int:
        """Depth of the deepest in-flight hub-initiated run of ``agent`` (0 if it was called by a human)."""
        with self._lock:
            depths = [r.depth for r in self._runs.values() if r.agent == agent]
        return max(depths, default=0)

    def _run(self, caller: str, target: str, message: str, action: str) -> str:
        depth = self._current_depth(caller) + 1
        if depth > self.max_depth:
            raise HubError(
                f"delegation depth limit ({self.max_depth}) reached; answer with what you have or escalate to a human"
            )
        run_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._runs[run_id] = _Run(target, depth)
        self._audit(caller, action, target=target, depth=depth, run_id=run_id, chars=len(message))
        try:
            answer = self.dispatch(foundry_name(self.org, target), message)
        except Exception as exc:  # surface failures to the calling agent instead of crashing the hub
            self._audit(caller, f"{action}_failed", target=target, run_id=run_id, error=str(exc))
            raise HubError(f"{target} could not complete the request: {exc}") from exc
        finally:
            with self._lock:
                self._runs.pop(run_id, None)
        self._audit(caller, f"{action}_done", target=target, run_id=run_id, chars=len(answer))
        return answer

    @staticmethod
    def _envelope(kind: str, sender: str, body: str, context: str = "") -> str:
        parts = [f"[{kind} from {sender} via HiveMind]", body.strip()]
        if context.strip():
            parts.append(f"Context:\n{context.strip()}")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ tools

    def list_colleagues(self, caller: str) -> list[dict[str, str]]:
        self._agent(caller)
        relation: dict[str, str] = {}
        manager = self.chart.manager(caller)
        if manager:
            relation[manager.name] = "manager (escalate)"
        for report in self.chart.reports(caller):
            relation[report.name] = "direct report (delegate)"
        for collaborator in self.chart.collaborators(caller):
            relation.setdefault(collaborator.name, "collaborator (delegate)")
        reviewer = self.chart.reviewer(caller)
        if reviewer:
            relation[reviewer.name] = relation.get(reviewer.name, "reviewer") + "; reviewer (request_review)"
        out = []
        for name, agent in sorted(self.chart.agents.items()):
            if name == caller:
                continue
            out.append(
                {
                    "name": name,
                    "title": agent.title,
                    "team": agent.team or "",
                    "description": agent.description.strip(),
                    "relationship": relation.get(name, "none (ask your manager to route work)"),
                }
            )
        return out

    def delegate_task(self, caller: str, to: str, task: str, context: str = "") -> str:
        self._agent(caller)
        if not self.chart.can_delegate(caller, to):
            allowed = ", ".join(a.name for a in self.chart.delegation_targets(caller)) or "nobody"
            raise HubError(f"{caller} may not delegate to '{to}'. Allowed: {allowed}")
        return self._run(caller, to, self._envelope("Task", caller, task, context), "delegate")

    def escalate(self, caller: str, issue: str, context: str = "") -> str:
        self._agent(caller)
        manager = self.chart.manager(caller)
        if manager is None:
            raise HubError(f"{caller} has no manager to escalate to; ask the human you are working with")
        return self._run(caller, manager.name, self._envelope("Escalation", caller, issue, context), "escalate")

    def request_review(self, caller: str, work: str, criteria: str = "") -> str:
        self._agent(caller)
        reviewer = self.chart.reviewer(caller)
        if reviewer is None:
            raise HubError(f"{caller} has no reviewer configured")
        body = work if not criteria else f"{work}\n\nReview criteria:\n{criteria}"
        message = self._envelope(
            "Review request", caller, body, "Reply with APPROVED or CHANGES REQUIRED, followed by your reasons."
        )
        return self._run(caller, reviewer.name, message, "review")

    def _scope_access(self, caller: str, scope: str, write: bool) -> None:
        access = memory_access(self.org, caller).get(scope)
        if access is None or (write and not access.write) or (not write and not access.read):
            readable = [s for s, a in memory_access(self.org, caller).items() if (a.write if write else a.read)]
            verb = "write to" if write else "read"
            raise HubError(f"{caller} may not {verb} memory scope '{scope}'. Allowed: {', '.join(readable) or 'none'}")

    def remember(self, caller: str, scope: str, content: str) -> dict[str, str]:
        self._agent(caller)
        self._scope_access(caller, scope, write=True)
        record = self.memory.write(scope, content, caller)
        self._audit(caller, "remember", scope=scope, memory_id=record.id, chars=len(content))
        return {"id": record.id, "scope": scope, "status": "stored"}

    def recall(self, caller: str, scope: str, query: str, limit: int = 5) -> list[dict[str, str]]:
        self._agent(caller)
        self._scope_access(caller, scope, write=False)
        records = self.memory.search(scope, query, limit=max(1, min(limit, 20)))
        self._audit(caller, "recall", scope=scope, hits=len(records))
        return [{"content": r.content, "author": r.author, "created_at": r.created_at} for r in records]
