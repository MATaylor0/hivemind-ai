from __future__ import annotations

import pytest

from hivemind.access import memory_access
from hivemind.hub import HubError, HubService, InMemoryBackend


class RecordingDispatcher:
    def __init__(self, reply: str = "done"):
        self.calls: list[tuple[str, str]] = []
        self.reply = reply
        self.on_call = None

    def __call__(self, agent: str, message: str) -> str:
        self.calls.append((agent, message))
        if self.on_call:
            return self.on_call(agent, message)
        return f"{self.reply} by {agent}"


@pytest.fixture
def hub(org):
    return HubService(org, InMemoryBackend(), RecordingDispatcher())


def test_manager_can_delegate_to_reports_only(hub):
    assert hub.delegate_task("finance-manager", "ap-clerk", "process invoice 42") == "done by ap-clerk"
    agent, message = hub.dispatch.calls[0]
    assert agent == "ap-clerk" and "[Task from finance-manager via HiveMind]" in message
    with pytest.raises(HubError, match="may not delegate to 'policy-assistant'"):
        hub.delegate_task("finance-manager", "policy-assistant", "answer a policy question")


def test_workers_cannot_delegate_upwards_but_can_reach_collaborators(hub):
    with pytest.raises(HubError):
        hub.delegate_task("ap-clerk", "finance-manager", "do my job")
    # collaboration is symmetric
    assert hub.delegate_task("ap-clerk", "fpa-analyst", "check budget for cost centre 210")
    assert hub.delegate_task("fpa-analyst", "ap-clerk", "is invoice 7 paid?")


def test_escalate_goes_to_manager(hub):
    hub.escalate("ap-clerk", "supplier bank details changed yesterday")
    assert hub.dispatch.calls[-1][0] == "finance-manager"
    with pytest.raises(HubError, match="no manager"):
        hub.escalate("chief-of-staff", "help")


def test_request_review_goes_to_reviewer(hub):
    hub.request_review("ap-clerk", "Approve invoice 99 for AUD 80,000", criteria="delegations of authority")
    agent, message = hub.dispatch.calls[-1]
    assert agent == "compliance-reviewer" and "APPROVED or CHANGES REQUIRED" in message
    with pytest.raises(HubError, match="no reviewer"):
        hub.request_review("fpa-analyst", "my forecast")


def test_delegation_depth_is_bounded(org):
    org.config.hub.max_delegation_depth = 2
    dispatcher = RecordingDispatcher()
    hub = HubService(org, InMemoryBackend(), dispatcher)

    # chief-of-staff -> finance-manager -> ap-clerk -> (fpa-analyst would be depth 3)
    def nested(agent: str, message: str) -> str:
        if agent == "finance-manager":
            return hub.delegate_task("finance-manager", "ap-clerk", "sub-task")
        if agent == "ap-clerk":
            return hub.delegate_task("ap-clerk", "fpa-analyst", "sub-sub-task")
        return "leaf"

    dispatcher.on_call = nested
    with pytest.raises(HubError, match="depth limit"):
        hub.delegate_task("chief-of-staff", "finance-manager", "top task")
    assert hub._runs == {}  # in-flight runs are always cleaned up


def test_dispatch_failures_are_returned_as_hub_errors(org):
    def boom(agent, message):
        raise RuntimeError("agent unavailable")

    hub = HubService(org, InMemoryBackend(), boom)
    with pytest.raises(HubError, match="could not complete the request: agent unavailable"):
        hub.delegate_task("finance-manager", "ap-clerk", "x")


def test_memory_scopes(org):
    ap = memory_access(org, "ap-clerk")
    assert ap["team:finance"].write and ap["month-end-close"].write
    assert ap["org"].read and not ap["org"].write

    cos = memory_access(org, "chief-of-staff")
    assert cos["org"].write
    assert cos["team:finance"].read and not cos["team:finance"].write  # manager-chain read access

    people = memory_access(org, "policy-assistant")
    assert "team:finance" not in people and "month-end-close" not in people


def test_remember_and_recall_respect_acls(hub):
    hub.remember("ap-clerk", "team:finance", "Supplier ACME sends duplicate invoices at quarter end")
    hits = hub.recall("finance-manager", "team:finance", "ACME duplicate")
    assert hits[0]["author"] == "ap-clerk" and "ACME" in hits[0]["content"]

    with pytest.raises(HubError, match="may not read memory scope 'team:finance'"):
        hub.recall("policy-assistant", "team:finance", "ACME")
    with pytest.raises(HubError, match="may not write to memory scope 'org'"):
        hub.remember("ap-clerk", "org", "everyone gets a pay rise")
    hub.remember("chief-of-staff", "org", "FY27 priority: reduce days payable outstanding")
    assert hub.recall("policy-assistant", "org", "priority")[0]["author"] == "chief-of-staff"


def test_unknown_caller_rejected(hub):
    with pytest.raises(HubError, match="unknown or disabled agent"):
        hub.list_colleagues("intruder")


def test_list_colleagues_describes_relationships(hub):
    colleagues = {c["name"]: c["relationship"] for c in hub.list_colleagues("ap-clerk")}
    assert colleagues["finance-manager"].startswith("manager")
    assert colleagues["fpa-analyst"].startswith("collaborator")
    assert "reviewer" in colleagues["compliance-reviewer"]
    assert "ap-clerk" not in colleagues


def test_mcp_server_exposes_hub_tools(org):
    pytest.importorskip("mcp")
    import asyncio

    from hivemind.hub.server import create_server

    server = create_server(HubService(org, InMemoryBackend(), RecordingDispatcher()), api_key="k")
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"list_colleagues", "delegate_task", "escalate", "request_review", "remember", "recall"}
