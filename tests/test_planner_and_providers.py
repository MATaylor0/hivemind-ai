from __future__ import annotations

from types import SimpleNamespace

import pytest

from hivemind.compiler import compile_org
from hivemind.loader import load_org
from hivemind.planner import apply_plan, make_plan
from hivemind.providers.local import LocalProvider

from .conftest import edit_yaml


def test_plan_apply_roundtrip(org_dir, tmp_path):
    provider = LocalProvider(tmp_path / "state")
    org = load_org(org_dir, env="local")

    plan = make_plan(compile_org(org), provider.list_managed())
    assert {i.action for i in plan.items} == {"create"}
    apply_plan(plan, provider, log=lambda _: None)

    plan = make_plan(compile_org(org), provider.list_managed())
    assert not plan.has_changes

    # Changing an agent's own instructions re-versions only that agent.
    job = org_dir / "instructions" / "agents" / "fpa-analyst.md"
    job.write_text(job.read_text() + "\n- Also produce the weekly cash forecast.\n")
    plan = make_plan(compile_org(load_org(org_dir, env="local")), provider.list_managed())
    assert [(i.action, i.name) for i in plan.items if i.action != "unchanged"] == [("update", "fpa-analyst")]
    apply_plan(plan, provider, log=lambda _: None)
    assert provider.list_managed()["fpa-analyst"].version == "2"

    # Changing its description also re-versions the colleagues whose instructions describe it.
    edit_yaml(org_dir / "agents" / "finance" / "fpa-analyst.yaml", description="Now also does cash forecasting.")
    plan = make_plan(compile_org(load_org(org_dir, env="local")), provider.list_managed())
    assert {i.name for i in plan.by_action("update")} == {"fpa-analyst", "finance-manager", "ap-clerk"}


def test_removed_agents_are_only_deleted_with_prune(org_dir, tmp_path):
    provider = LocalProvider(tmp_path / "state")
    org = load_org(org_dir, env="local")
    apply_plan(make_plan(compile_org(org), {}), provider, log=lambda _: None)

    edit_yaml(org_dir / "agents" / "people" / "policy-assistant.yaml", enabled=False)
    plan = make_plan(compile_org(load_org(org_dir, env="local")), provider.list_managed())
    assert [i.name for i in plan.by_action("delete")] == ["policy-assistant"]

    apply_plan(plan, provider, prune=False, log=lambda _: None)
    assert "policy-assistant" in provider.list_managed()
    apply_plan(plan, provider, prune=True, log=lambda _: None)
    assert "policy-assistant" not in provider.list_managed()


# ------------------------------------------------------------------ Foundry provider with a fake SDK client


class FakeAgents:
    def __init__(self):
        self.created = []
        self.existing = [
            SimpleNamespace(name="legacy-bot", versions=SimpleNamespace(latest=SimpleNamespace(metadata={}))),
            SimpleNamespace(
                name="ap-clerk",
                versions=SimpleNamespace(
                    latest=SimpleNamespace(
                        version="3",
                        metadata={"managed_by": "hivemind", "hm_hash": "abc", "hm_spec": "ap-clerk"},
                        definition={"model": "gpt-5-mini"},
                        created_at=None,
                    )
                ),
            ),
        ]

    def list(self):
        return self.existing

    def create_version(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(version="4")


class FakeConnections:
    def __init__(self):
        self.lookups = []

    def get(self, name):
        self.lookups.append(name)
        return SimpleNamespace(id=f"/subscriptions/s/resourceGroups/rg/connections/{name}")


def test_foundry_provider_lists_only_managed_agents_and_resolves_connections(org):
    pytest.importorskip("azure.ai.projects")
    from hivemind.providers.foundry import FoundryProvider

    client = SimpleNamespace(agents=FakeAgents(), connections=FakeConnections())
    provider = FoundryProvider("unused", client=client)

    managed = provider.list_managed()
    assert list(managed) == ["ap-clerk"] and managed["ap-clerk"].content_hash == "abc"

    build = next(b for b in compile_org(org) if b.name == "ap-clerk")
    deployed = provider.deploy(build)
    assert deployed.version == "4"

    sent = client.agents.created[0]
    assert sent["agent_name"] == "ap-clerk" and sent["metadata"]["managed_by"] == "hivemind"
    definition = dict(sent["definition"])
    ids = [t.get("project_connection_id") for t in definition["tools"] if t.get("project_connection_id")]
    assert ids and all(i.startswith("/subscriptions/") for i in ids)
    # each connection name is looked up once and cached
    assert len(client.connections.lookups) == len(set(client.connections.lookups))
    # the build itself is not mutated
    assert any(t.get("project_connection_id") == "erp-mcp" for t in build.definition["tools"])
