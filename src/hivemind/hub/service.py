"""The HiveMind hub: governed agent-to-agent collaboration, task queue, shared memory and outputs.

Every Foundry agent built by HiveMind gets the hub as an MCP tool. The hub knows the org chart (it loads the
same org directory as the CLI), so it enforces the same rules the agents were told about:

* ``delegate_task``  - only to direct reports and declared collaborators.
* ``escalate``       - only to your manager.
* ``request_review`` - only to your reviewer.
* ``remember/recall``- only in memory scopes your role, team and opt-ins allow.
* ``save_output``    - into your own folder of the outputs library.

Delegation, escalation and review create **tasks** in a queue (see ``hivemind.tasks``). The caller waits up to
``hub.tasks.wait_seconds`` for the result; if the work takes longer, the caller gets a task id and the result
arrives later as a follow-up task. Loops are bounded by delegation depth and by a cap on tasks per request.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..access import memory_access
from ..hierarchy import OrgChart
from ..instructions import goal_chain
from ..loader import Org
from ..tasks import InMemoryTaskStore, Task, TaskStore
from ..workspace import Workspace, output_path, render_output
from .memory import MemoryBackend

audit_log = logging.getLogger("hivemind.hub.audit")

# Runs an agent (by Foundry name) with a message and returns its text answer.
Dispatcher = Callable[[str, str], str]


class HubError(Exception):
    """Raised for calls the org chart does not allow. The message is returned to the calling agent."""


class HubService:
    def __init__(
        self,
        org: Org,
        memory: MemoryBackend,
        dispatch: Dispatcher,
        tasks: TaskStore | None = None,
        workspace: Workspace | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.org = org
        self.memory = memory
        self.dispatch = dispatch
        self.tasks: TaskStore = tasks or InMemoryTaskStore()
        self.workspace = workspace
        self.sleep = sleep
        self.chart = OrgChart(org.active_agents())
        self.config = org.config.hub.tasks
        self.max_depth = org.config.hub.max_delegation_depth

    # ------------------------------------------------------------------ helpers

    def _agent(self, caller: str) -> None:
        if caller not in self.chart.agents:
            raise HubError(f"unknown or disabled agent '{caller}'")

    def audit(self, caller: str, action: str, **fields: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(), "caller": caller, "action": action, **fields}
        audit_log.info(json.dumps(record, default=str))

    def current_task(self, caller: str, task_id: str | None = None) -> Task | None:
        """The task ``caller`` is working on: the one it names, else its most recent in-progress task."""
        if task_id:
            task = self.tasks.get(task_id)
            if task is None or task.assigned_to != caller:
                raise HubError(f"task '{task_id}' is not assigned to {caller}")
            return task
        running = self.tasks.query(status="InProgress", assigned_to=caller)
        return running[-1] if running else None

    def goal_text(self, goal: str | None) -> str:
        if not goal:
            return ""
        chain = goal_chain(self.org, goal)
        return " → ".join(self.org.config.goals[g].title for g in chain) if chain else goal

    def _enqueue(
        self,
        caller: str,
        target: str,
        kind: str,
        title: str,
        body: str,
        parent_task: str | None,
        goal: str | None,
        wait_seconds: int | None,
    ) -> dict[str, Any]:
        parent = self.current_task(caller, parent_task)
        depth = (parent.depth if parent else 0) + 1
        if depth > self.max_depth:
            raise HubError(
                f"delegation depth limit ({self.max_depth}) reached; answer with what you have or escalate to a human"
            )
        root_id = parent.root_id if parent else None
        if root_id and len(self.tasks.query(root_id=root_id, limit=1000)) >= self.config.max_tasks_per_request:
            raise HubError(
                f"this request has already spawned {self.config.max_tasks_per_request} tasks; "
                "finish with what you have or ask a human"
            )
        task = self.tasks.create(
            Task(
                title=title[:250],
                instructions=body,
                assigned_to=target,
                requested_by=caller,
                requester_kind="agent",
                kind=kind,  # type: ignore[arg-type]
                parent_id=parent.id if parent else None,
                root_id=root_id,
                goal=goal or (parent.goal if parent else None),
                depth=depth,
            )
        )
        self.audit(caller, kind, task_id=task.id, target=target, depth=depth, parent=task.parent_id)
        return self._await(caller, task, self.config.wait_seconds if wait_seconds is None else wait_seconds)

    def _await(self, caller: str, task: Task, wait_seconds: int) -> dict[str, Any]:
        wait_seconds = max(0, min(wait_seconds, 300))
        deadline = time.monotonic() + wait_seconds
        current = task
        while True:
            current = self.tasks.get(task.id) or current
            if current.status in ("Done", "Failed", "Cancelled"):
                # Deliver exactly once: if the worker already sent the result as a follow-up, say so.
                claimed = self.tasks.transition(current, delivered=True) if not current.delivered else None
                if claimed is not None:
                    return current.summary()
                return {**current.summary(), "note": "result already delivered to you as a follow-up task"}
            if time.monotonic() >= deadline:
                return {
                    **current.summary(),
                    "note": "still in progress - the result will reach you as a follow-up task, or use check_task",
                }
            self.sleep(min(2.0, max(0.05, deadline - time.monotonic())))

    @staticmethod
    def _title(text: str) -> str:
        first = text.strip().splitlines()[0] if text.strip() else "Task"
        return first[:120]

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
        return [
            {
                "name": name,
                "title": agent.title,
                "team": agent.team or "",
                "description": agent.description.strip(),
                "relationship": relation.get(name, "none (ask your manager to route work)"),
            }
            for name, agent in sorted(self.chart.agents.items())
            if name != caller
        ]

    def delegate_task(
        self,
        caller: str,
        to: str,
        task: str,
        context: str = "",
        parent_task: str | None = None,
        goal: str | None = None,
        wait_seconds: int | None = None,
    ) -> dict[str, Any]:
        self._agent(caller)
        if not self.chart.can_delegate(caller, to):
            allowed = ", ".join(a.name for a in self.chart.delegation_targets(caller)) or "nobody"
            raise HubError(f"{caller} may not delegate to '{to}'. Allowed: {allowed}")
        if goal and goal not in self.org.config.goals:
            raise HubError(f"unknown goal '{goal}'. Goals: {', '.join(self.org.config.goals) or 'none'}")
        body = task.strip() + (f"\n\nContext:\n{context.strip()}" if context.strip() else "")
        return self._enqueue(caller, to, "task", self._title(task), body, parent_task, goal, wait_seconds)

    def escalate(
        self,
        caller: str,
        issue: str,
        context: str = "",
        parent_task: str | None = None,
        wait_seconds: int | None = None,
    ) -> dict[str, Any]:
        self._agent(caller)
        manager = self.chart.manager(caller)
        if manager is None:
            raise HubError(f"{caller} has no manager to escalate to; ask the human you are working with")
        body = issue.strip() + (f"\n\nContext:\n{context.strip()}" if context.strip() else "")
        return self._enqueue(
            caller,
            manager.name,
            "escalation",
            f"Escalation: {self._title(issue)}",
            body,
            parent_task,
            None,
            wait_seconds,
        )

    def request_review(
        self,
        caller: str,
        work: str,
        criteria: str = "",
        parent_task: str | None = None,
        wait_seconds: int | None = None,
    ) -> dict[str, Any]:
        self._agent(caller)
        reviewer = self.chart.reviewer(caller)
        if reviewer is None:
            raise HubError(f"{caller} has no reviewer configured")
        body = work.strip()
        if criteria.strip():
            body += f"\n\nReview criteria:\n{criteria.strip()}"
        body += "\n\nReply with APPROVED or CHANGES REQUIRED, followed by your reasons."
        return self._enqueue(
            caller, reviewer.name, "review", f"Review: {self._title(work)}", body, parent_task, None, wait_seconds
        )

    def check_task(self, caller: str, task_id: str) -> dict[str, Any]:
        self._agent(caller)
        task = self.tasks.get(task_id)
        if task is None or caller not in (task.requested_by, task.assigned_to):
            raise HubError(f"task '{task_id}' not found among tasks you requested or were assigned")
        if task.requested_by == caller and task.status in ("Done", "Failed") and not task.delivered:
            self.tasks.transition(task, delivered=True)
        return task.summary()

    def my_tasks(self, caller: str) -> dict[str, list[dict[str, str]]]:
        self._agent(caller)
        assigned = [t for t in self.tasks.query(assigned_to=caller) if t.status in ("Queued", "InProgress")]
        requested = [t for t in self.tasks.query(requested_by=caller) if t.status in ("Queued", "InProgress")]
        return {"assigned_to_me": [t.summary() for t in assigned], "requested_by_me": [t.summary() for t in requested]}

    def save_output(self, caller: str, title: str, content: str, task_id: str | None = None) -> dict[str, str]:
        self._agent(caller)
        if self.workspace is None or self.org.config.sharepoint is None:
            raise HubError("no SharePoint outputs library is configured for this organisation")
        fmt = self.org.config.sharepoint.output_format
        saved = self.workspace.save_output(
            output_path(self.org, caller, title, fmt), render_output(content, title, fmt)
        )
        task = self.current_task(caller, task_id)
        if task is not None:
            self.tasks.update(task, output_url=saved.url)
        self.audit(caller, "save_output", path=saved.path, task_id=task.id if task else None)
        return {"title": title, "url": saved.url, "path": saved.path}

    def _scope_access(self, caller: str, scope: str, write: bool) -> None:
        access = memory_access(self.org, caller).get(scope)
        if access is None or (write and not access.write) or (not write and not access.read):
            allowed = [s for s, a in memory_access(self.org, caller).items() if (a.write if write else a.read)]
            verb = "write to" if write else "read"
            raise HubError(f"{caller} may not {verb} memory scope '{scope}'. Allowed: {', '.join(allowed) or 'none'}")

    def remember(self, caller: str, scope: str, content: str) -> dict[str, str]:
        self._agent(caller)
        self._scope_access(caller, scope, write=True)
        record = self.memory.write(scope, content, caller)
        self.audit(caller, "remember", scope=scope, memory_id=record.id, chars=len(content))
        return {"id": record.id, "scope": scope, "status": "stored"}

    def recall(self, caller: str, scope: str, query: str, limit: int = 5) -> list[dict[str, str]]:
        self._agent(caller)
        self._scope_access(caller, scope, write=False)
        records = self.memory.search(scope, query, limit=max(1, min(limit, 20)))
        self.audit(caller, "recall", scope=scope, hits=len(records))
        return [{"content": r.content, "author": r.author, "created_at": r.created_at} for r in records]
