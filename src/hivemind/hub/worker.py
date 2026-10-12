"""The task runner: claims queued tasks, runs the assigned Foundry agent, records results, delivers follow-ups.

Runs inside each hub replica (``hivemind hub serve``). Claims use compare-and-set, so replicas never work the
same task twice. A task whose worker dies is picked up again when its lease expires, up to ``max_attempts``.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from ..compiler import foundry_name
from ..tasks import Task, lease, now
from .service import HubService

log = logging.getLogger("hivemind.hub.worker")


class TaskRunner:
    def __init__(self, service: HubService):
        self.service = service
        self.config = service.config
        self._running: Counter[str] = Counter()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ one task

    def envelope(self, task: Task) -> str:
        """The message the assigned agent receives."""
        origin = f"{task.requested_by} ({task.requester_kind})"
        lines = [f"[Task {task.id} from {origin} via HiveMind] {task.title}"]
        if task.kind == "follow_up":
            lines[0] = f"[Task {task.id}: follow-up from HiveMind] {task.title}"
        goal = self.service.goal_text(task.goal)
        if goal:
            lines.append(f"Goal: {goal}")
        if task.parent_id:
            parent = self.service.tasks.get(task.parent_id)
            if parent:
                lines.append(f"This serves {parent.id} ({parent.title}), assigned to {parent.assigned_to}.")
        lines.append("")
        lines.append(task.instructions.strip())
        lines.append("")
        lines.append(
            f"When you delegate, escalate or request review for this work, pass parent_task='{task.id}'. "
            "Your final answer is recorded as the task result."
        )
        return "\n".join(lines)

    def execute(self, task: Task) -> Task:
        service = self.service
        if task.assigned_to not in service.chart.agents:
            return service.tasks.update(task, status="Failed", error=f"'{task.assigned_to}' is not an active agent")
        try:
            answer = service.dispatch(foundry_name(service.org, task.assigned_to), self.envelope(task))
        except Exception as exc:  # noqa: BLE001 - any agent failure is recorded on the task
            final = task.attempts >= self.config.max_attempts
            service.audit(task.assigned_to, "task_failed", task_id=task.id, attempt=task.attempts, error=str(exc))
            return service.tasks.update(
                task,
                status="Failed" if final else "Queued",
                error=str(exc)[:2000],
                lease_until=None,
            )
        service.audit(task.assigned_to, "task_done", task_id=task.id, chars=len(answer))
        return service.tasks.update(task, status="Done", result=answer, error="", lease_until=None)

    # ------------------------------------------------------------------ claiming

    def _claimable(self) -> list[Task]:
        tasks = self.service.tasks
        queued = tasks.query(status="Queued", limit=50)
        expired = [t for t in tasks.query(status="InProgress", limit=50) if t.lease_until and t.lease_until < now()]
        return sorted(queued + expired, key=lambda t: t.created_at)

    def claim(self) -> list[Task]:
        """Claim as many tasks as there are free worker slots, respecting each agent's concurrency limit."""
        claimed: list[Task] = []
        slots = self.config.workers - sum(self._running.values())
        for task in self._claimable():
            if slots <= 0:
                break
            spec = self.service.org.agents.get(task.assigned_to)
            limit = spec.max_concurrent_tasks if spec else 1
            with self._lock:
                if self._running[task.assigned_to] >= limit:
                    continue
            if task.attempts >= self.config.max_attempts:
                self.service.tasks.transition(task, status="Failed", error=task.error or "lease expired too often")
                continue
            won = self.service.tasks.transition(
                task, status="InProgress", attempts=task.attempts + 1, lease_until=lease(self.config.lease_minutes)
            )
            if won is None:
                continue  # another replica claimed it
            with self._lock:
                self._running[won.assigned_to] += 1
            claimed.append(won)
            slots -= 1
        return claimed

    def _run_claimed(self, task: Task) -> None:
        try:
            self.execute(task)
        finally:
            with self._lock:
                self._running[task.assigned_to] -= 1

    # ------------------------------------------------------------------ follow-ups

    def deliver_follow_ups(self) -> int:
        """Results nobody waited for go back to the requesting agent as a new task."""
        grace = timedelta(seconds=self.config.wait_seconds + 5)
        delivered = 0
        for status in ("Done", "Failed"):
            for task in self.service.tasks.query(status=status, limit=200):
                if task.delivered or task.requester_kind != "agent" or now() - task.updated_at < grace:
                    continue
                if task.requested_by not in self.service.chart.agents:
                    continue
                if self.service.tasks.transition(task, delivered=True) is None:
                    continue
                outcome = task.result if status == "Done" else f"FAILED: {task.error}"
                parent = self.service.tasks.get(task.parent_id) if task.parent_id else None
                self.service.tasks.create(
                    Task(
                        title=f"Result of {task.id}: {task.title}"[:250],
                        instructions=(
                            f"{task.assigned_to} finished {task.id} ({task.title}) that you delegated.\n\n"
                            f"Result:\n{outcome}\n\n"
                            + (
                                f"Continue {parent.id} ({parent.title}) with this result."
                                if parent
                                else "Use this result to continue your work."
                            )
                        ),
                        assigned_to=task.requested_by,
                        requested_by="hivemind",
                        requester_kind="person",
                        kind="follow_up",
                        parent_id=parent.id if parent else None,
                        root_id=task.root_id,
                        goal=task.goal,
                        depth=parent.depth if parent else task.depth,
                    )
                )
                delivered += 1
        return delivered

    # ------------------------------------------------------------------ loops

    def run_pending(self) -> int:
        """Synchronously work everything claimable (tests, one-off CLI runs). Returns tasks executed."""
        count = 0
        while True:
            batch = self.claim()
            if not batch:
                return count
            for task in batch:
                self._run_claimed(task)
                count += 1

    def serve(self, stop: threading.Event) -> None:
        log.info("task runner started: %d workers, polling every %ss", self.config.workers, self.config.poll_seconds)
        with ThreadPoolExecutor(max_workers=max(1, self.config.workers), thread_name_prefix="hm-task") as pool:
            while not stop.is_set():
                try:
                    for task in self.claim():
                        pool.submit(self._run_claimed, task)
                    self.deliver_follow_ups()
                except Exception:  # noqa: BLE001 - keep polling through transient store errors
                    log.exception("task runner poll failed")
                stop.wait(self.config.poll_seconds)
