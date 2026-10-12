from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from hivemind.access import memory_access
from hivemind.hub import HubError, HubService, InMemoryBackend
from hivemind.hub.worker import TaskRunner
from hivemind.tasks import InMemoryTaskStore, Task, now
from hivemind.workspace import LocalWorkspace


class RecordingDispatcher:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.on_call = None

    def __call__(self, agent: str, message: str) -> str:
        self.calls.append((agent, message))
        if self.on_call:
            return self.on_call(agent, message)
        return f"done by {agent}"


@pytest.fixture
def hub(org):
    return HubService(org, InMemoryBackend(), RecordingDispatcher(), tasks=InMemoryTaskStore(), sleep=lambda _: None)


def person_task(hub, agent, text="do something", goal=None):
    return hub.tasks.create(
        Task(
            title=text,
            instructions=text,
            assigned_to=agent,
            requested_by="cfo@contoso.com",
            requester_kind="person",
            depth=0,
            goal=goal,
        )
    )


# ------------------------------------------------------------------ delegation rules


def test_delegation_creates_a_task_that_the_runner_works(hub):
    out = hub.delegate_task("finance-manager", "ap-clerk", "Process invoice 42", wait_seconds=0)
    assert out["status"] == "Queued" and out["assigned_to"] == "ap-clerk"
    assert TaskRunner(hub).run_pending() == 1
    task = hub.tasks.get(out["task_id"])
    assert task.status == "Done" and task.result == "done by ap-clerk"
    agent, message = hub.dispatch.calls[0]
    assert agent == "ap-clerk" and message.startswith(f"[Task {task.id} from finance-manager (agent) via HiveMind]")


def test_delegation_rules_follow_the_org_chart(hub):
    with pytest.raises(HubError, match="may not delegate to 'policy-assistant'. Allowed: ap-clerk, fpa-analyst"):
        hub.delegate_task("finance-manager", "policy-assistant", "x", wait_seconds=0)
    with pytest.raises(HubError):
        hub.delegate_task("ap-clerk", "finance-manager", "do my job", wait_seconds=0)
    # collaboration is symmetric
    hub.delegate_task("ap-clerk", "fpa-analyst", "check budget", wait_seconds=0)
    hub.delegate_task("fpa-analyst", "ap-clerk", "is invoice 7 paid?", wait_seconds=0)


def test_escalation_and_review_are_routed(hub):
    esc = hub.escalate("ap-clerk", "Bank details changed yesterday", wait_seconds=0)
    rev = hub.request_review("ap-clerk", "Approve invoice 99 for AUD 80,000", criteria="DoA", wait_seconds=0)
    assert hub.tasks.get(esc["task_id"]).assigned_to == "finance-manager"
    review = hub.tasks.get(rev["task_id"])
    assert review.assigned_to == "compliance-reviewer" and review.kind == "review"
    assert "APPROVED or CHANGES REQUIRED" in review.instructions
    with pytest.raises(HubError, match="no manager"):
        hub.escalate("chief-of-staff", "help")
    with pytest.raises(HubError, match="no reviewer"):
        hub.request_review("fpa-analyst", "my forecast")


def test_waiting_caller_gets_the_result_and_no_follow_up(org):
    service = HubService(org, InMemoryBackend(), RecordingDispatcher(), tasks=InMemoryTaskStore())
    runner = TaskRunner(service)
    stop = threading.Event()
    service.config.poll_seconds = 0.05
    thread = threading.Thread(target=runner.serve, args=(stop,), daemon=True)
    thread.start()
    try:
        out = service.delegate_task("finance-manager", "ap-clerk", "Process invoice 42", wait_seconds=10)
    finally:
        stop.set()
        thread.join(5)
    assert out["status"] == "Done" and out["result"] == "done by ap-clerk" and "note" not in out
    assert service.tasks.get(out["task_id"]).delivered
    assert runner.deliver_follow_ups() == 0


def test_unwaited_results_come_back_as_follow_up_tasks(hub):
    parent = person_task(hub, "finance-manager", "Close status for the CFO", goal="fast-close")
    runner = TaskRunner(hub)
    hub.config.wait_seconds = 0

    def work(agent, message):
        if agent == "finance-manager" and "Close status" in message:
            hub.delegate_task("finance-manager", "ap-clerk", "AP cut-off status?", wait_seconds=0)
            return "asked AP"
        return "AP cut-off complete, 3 invoices on hold"

    hub.dispatch.on_call = work
    runner.run_pending()
    child = hub.tasks.query(assigned_to="ap-clerk")[0]
    assert child.parent_id == parent.id and child.root_id == parent.id and child.goal == "fast-close"
    assert child.depth == 1

    # pretend the grace period has passed
    hub.tasks._tasks[child.id].updated_at = now() - timedelta(minutes=5)
    assert runner.deliver_follow_ups() == 1
    follow_up = [t for t in hub.tasks.query(assigned_to="finance-manager") if t.kind == "follow_up"][0]
    assert "3 invoices on hold" in follow_up.instructions and follow_up.parent_id == parent.id
    assert runner.deliver_follow_ups() == 0  # delivered exactly once


def test_envelope_carries_goal_ancestry_and_parent(hub):
    parent = person_task(hub, "finance-manager", "Close status", goal="fast-close")
    seen = {}

    def work(agent, message):
        seen[agent] = message
        if agent == "finance-manager":
            hub.delegate_task("finance-manager", "ap-clerk", "AP status", wait_seconds=0)
        return "ok"

    hub.dispatch.on_call = work
    TaskRunner(hub).run_pending()
    assert "Goal: Close the books within 3 business days → Lift FY27 operating margin to 18%" in seen["ap-clerk"]
    assert f"This serves {parent.id} (Close status)" in seen["ap-clerk"]


def test_delegation_depth_is_bounded_durably(org):
    org.config.hub.max_delegation_depth = 2
    service = HubService(org, InMemoryBackend(), RecordingDispatcher(), tasks=InMemoryTaskStore(), sleep=lambda _: None)
    errors = []

    def work(agent, message):
        chain = {"chief-of-staff": "finance-manager", "finance-manager": "ap-clerk", "ap-clerk": "fpa-analyst"}
        try:
            service.delegate_task(agent, chain[agent], f"sub-task from {agent}", wait_seconds=0)
        except HubError as exc:
            errors.append((agent, str(exc)))
        return "ok"

    service.dispatch.on_call = work
    person_task(service, "chief-of-staff", "Top request")
    TaskRunner(service).run_pending()
    assert errors == [("ap-clerk", errors[0][1])] and "depth limit" in errors[0][1]


def test_tasks_per_request_are_capped(hub):
    hub.config.max_tasks_per_request = 3
    root = person_task(hub, "finance-manager")
    claimed = hub.tasks.transition(root, status="InProgress")
    assert claimed
    hub.delegate_task("finance-manager", "ap-clerk", "one", wait_seconds=0)
    hub.delegate_task("finance-manager", "ap-clerk", "two", wait_seconds=0)
    with pytest.raises(HubError, match="already spawned 3 tasks"):
        hub.delegate_task("finance-manager", "ap-clerk", "three", wait_seconds=0)


def test_failed_dispatch_is_retried_then_failed(hub):
    hub.dispatch.on_call = lambda agent, message: (_ for _ in ()).throw(RuntimeError("agent unavailable"))
    out = hub.delegate_task("finance-manager", "ap-clerk", "x", wait_seconds=0)
    TaskRunner(hub).run_pending()
    task = hub.tasks.get(out["task_id"])
    assert task.status == "Failed" and task.attempts == 3 and "agent unavailable" in task.error


def test_expired_lease_is_reclaimed(hub):
    out = hub.delegate_task("finance-manager", "ap-clerk", "x", wait_seconds=0)
    task = hub.tasks.get(out["task_id"])
    hub.tasks.transition(task, status="InProgress", attempts=1, lease_until=now() - timedelta(minutes=1))
    assert TaskRunner(hub).run_pending() == 1
    assert hub.tasks.get(task.id).status == "Done"


def test_claims_are_exclusive(hub):
    out = hub.delegate_task("finance-manager", "ap-clerk", "x", wait_seconds=0)
    task = hub.tasks.get(out["task_id"])
    assert hub.tasks.transition(task, status="InProgress") is not None
    assert hub.tasks.transition(task, status="InProgress") is None  # stale etag loses


def test_check_task_and_my_tasks(hub):
    out = hub.delegate_task("finance-manager", "ap-clerk", "x", wait_seconds=0)
    assert hub.my_tasks("ap-clerk")["assigned_to_me"][0]["task_id"] == out["task_id"]
    assert hub.my_tasks("finance-manager")["requested_by_me"][0]["task_id"] == out["task_id"]
    with pytest.raises(HubError, match="not found among tasks"):
        hub.check_task("policy-assistant", out["task_id"])
    TaskRunner(hub).run_pending()
    assert hub.check_task("finance-manager", out["task_id"])["result"] == "done by ap-clerk"
    assert hub.tasks.get(out["task_id"]).delivered


def test_save_output_writes_word_document_and_links_task(org, tmp_path):
    workspace = LocalWorkspace(tmp_path, "Agent Rules", "Agent Outputs")
    service = HubService(org, InMemoryBackend(), RecordingDispatcher(), tasks=InMemoryTaskStore(), workspace=workspace)
    task = person_task(service, "fpa-analyst", "Variance pack")
    service.tasks.transition(task, status="InProgress")
    saved = service.save_output("fpa-analyst", "Q1 variance pack", "# Summary\n- Revenue up 4%")
    assert saved["path"].startswith("finance/fpa-analyst/") and saved["path"].endswith("Q1 variance pack.docx")
    assert (tmp_path / "Agent Outputs" / saved["path"]).read_bytes()[:2] == b"PK"
    assert service.tasks.get(task.id).output_url == saved["url"]


# ------------------------------------------------------------------ memory


def test_memory_scopes(org):
    ap = memory_access(org, "ap-clerk")
    assert ap["team:finance"].write and ap["month-end-close"].write
    assert ap["org"].read and not ap["org"].write
    cos = memory_access(org, "chief-of-staff")
    assert cos["org"].write
    assert cos["team:finance"].read and not cos["team:finance"].write
    assert "team:finance" not in memory_access(org, "policy-assistant")


def test_remember_and_recall_respect_acls(hub):
    hub.remember("ap-clerk", "team:finance", "Supplier ACME sends duplicate invoices at quarter end")
    assert hub.recall("finance-manager", "team:finance", "ACME duplicate")[0]["author"] == "ap-clerk"
    with pytest.raises(HubError, match="may not read memory scope 'team:finance'"):
        hub.recall("policy-assistant", "team:finance", "ACME")
    with pytest.raises(HubError, match="may not write to memory scope 'org'"):
        hub.remember("ap-clerk", "org", "everyone gets a pay rise")


def test_unknown_caller_rejected(hub):
    with pytest.raises(HubError, match="unknown or disabled agent"):
        hub.list_colleagues("intruder")


def test_list_colleagues_describes_relationships(hub):
    colleagues = {c["name"]: c["relationship"] for c in hub.list_colleagues("ap-clerk")}
    assert colleagues["finance-manager"].startswith("manager")
    assert colleagues["fpa-analyst"].startswith("collaborator")
    assert "reviewer" in colleagues["compliance-reviewer"]


# ------------------------------------------------------------------ MCP surface


def test_mcp_server_exposes_hub_tools(hub):
    pytest.importorskip("mcp")
    import asyncio

    from hivemind.hub.auth import KeyAuthenticator
    from hivemind.hub.server import create_server

    server = create_server(hub, KeyAuthenticator("k"))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {
        "list_colleagues",
        "delegate_task",
        "escalate",
        "request_review",
        "check_task",
        "my_tasks",
        "save_output",
        "remember",
        "recall",
    }


def test_wait_returns_task_id_when_work_is_slow(org):
    clock = {"t": 0.0}
    service = HubService(
        org,
        InMemoryBackend(),
        RecordingDispatcher(),
        tasks=InMemoryTaskStore(),
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    start = time.monotonic()
    out = service.delegate_task("finance-manager", "ap-clerk", "x", wait_seconds=1)
    assert out["status"] == "Queued" and "follow-up" in out["note"]
    assert time.monotonic() - start < 5
