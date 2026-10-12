from __future__ import annotations

from hivemind.compiler import USER_SCOPE, compile_agent, effective_tool_ids
from hivemind.instructions import SourceResolver
from hivemind.loader import load_org

from .conftest import edit_yaml


def tools_by_type(build):
    out: dict[str, list[dict]] = {}
    for tool in build.definition["tools"]:
        out.setdefault(tool["type"], []).append(tool)
    return out


def test_inheritance_order_org_role_team_agent(org):
    # defaults.knowledge -> role tools -> team knowledge -> agent tools
    assert effective_tool_ids(org, org.agents["ap-clerk"]) == [
        "org-policies",
        "finance-handbook",
        "erp-invoices",
        "fabric-finance-warehouse",
    ]
    assert effective_tool_ids(org, org.agents["chief-of-staff"]) == [
        "org-policies",
        "work-iq",
        "policy-library",
        "fabric-sales-agent",
        "work-iq-mail",
    ]


def test_instruction_layers_are_composed_in_order(org):
    text = compile_agent(org, "ap-clerk").instructions
    order = [
        "Organisation guidance",
        "Your role: worker",
        "Team: finance",
        "Your job",
        "Operating mode",
        "Goals",
        "Processes",
        "Your position in the organisation",
        "Tools and knowledge",
        "When to stop and escalate",
    ]
    positions = [text.index(f"## {heading}") for heading in order]
    assert positions == sorted(positions)
    assert "three-way match" in text  # agent-specific
    assert "Act reversibly" in text  # org constitution


def test_org_position_is_generated_from_hierarchy(org):
    manager = compile_agent(org, "finance-manager").instructions
    assert "Your direct reports" in manager and "`ap-clerk`" in manager and "`fpa-analyst`" in manager
    assert "You report to:\n- **Chief of Staff**" in manager
    reviewer = compile_agent(org, "compliance-reviewer").instructions
    assert "You review work produced by" in reviewer


def test_foundry_tool_shapes(org):
    sharepoint = tools_by_type(compile_agent(org, "policy-assistant"))["sharepoint_grounding_preview"][0]
    connections = sharepoint["sharepoint_grounding_preview"]["project_connections"]
    assert connections == [{"project_connection_id": "sp-policies"}]
    tools = tools_by_type(compile_agent(org, "ap-clerk"))
    indexes = [t["azure_ai_search"]["indexes"][0]["index_name"] for t in tools["azure_ai_search"]]
    assert indexes == ["policies", "finance-handbook"]
    erp = next(t for t in tools["mcp"] if t["server_label"] == "erp_invoices")
    assert erp["require_approval"] == "always"  # ap-clerk is at 'approve': every ERP change needs a person
    assert erp["project_connection_id"] == "erp-mcp"


def test_private_memory_scopes(org):
    cos = tools_by_type(compile_agent(org, "chief-of-staff"))["memory_search_preview"][0]
    assert cos["scope"] == USER_SCOPE and cos["memory_store_name"] == "chief-of-staff-memory"
    ap = tools_by_type(compile_agent(org, "ap-clerk"))["memory_search_preview"][0]
    assert ap["scope"] == "ap-clerk"
    assert "memory_search_preview" not in tools_by_type(compile_agent(org, "compliance-reviewer"))


def test_hash_is_stable_and_changes_with_content(org_dir):
    org = load_org(org_dir, env="local")
    first = compile_agent(org, "fpa-analyst").content_hash
    assert compile_agent(load_org(org_dir, env="local"), "fpa-analyst").content_hash == first

    # A change to a shared layer (team instructions) changes every member's hash.
    team_md = org_dir / "instructions" / "teams" / "finance.md"
    team_md.write_text(team_md.read_text() + "\nNew rule.\n")
    assert compile_agent(load_org(org_dir, env="local"), "fpa-analyst").content_hash != first


def test_schedule_change_changes_hash(org_dir):
    org = load_org(org_dir, env="local")
    before = compile_agent(org, "ap-clerk").content_hash
    path = org_dir / "agents" / "finance" / "ap-clerk.yaml"
    import yaml

    data = yaml.safe_load(path.read_text())
    data["schedules"][0]["cron"] = "0 9 * * 1-5"
    path.write_text(yaml.safe_dump(data))
    assert compile_agent(load_org(org_dir, env="local"), "ap-clerk").content_hash != before


def test_environment_prefix_and_model_override(org_dir, monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENDPOINT_DEV", "https://x/api/projects/dev")
    org = load_org(org_dir, env="dev")
    build = compile_agent(org, "finance-manager")
    assert build.name == "dev-finance-manager"
    assert build.definition["model"] == "gpt-5-mini"  # gpt-5 overridden in dev
    assert build.metadata["hm_spec"] == "finance-manager"
    assert build.memory_store.name == "dev-finance-manager-memory"


def test_metadata_fits_foundry_limits(org):
    for name in org.agents:
        meta = compile_agent(org, name, git_sha="abc123").metadata
        assert len(meta) <= 16
        assert all(len(k) <= 64 and len(v) <= 512 for k, v in meta.items())
        assert meta["managed_by"] == "hivemind"


def test_sharepoint_sources_are_fetched_with_resolver(org_dir):
    path = org_dir / "agents" / "finance" / "ap-clerk.yaml"
    edit_yaml(
        path,
        instructions=[
            {"file": "instructions/agents/ap-clerk.md"},
            {"sharepoint": {"site": "contoso.sharepoint.com/sites/Finance", "path": "Agents/exceptions.md"}},
        ],
    )
    org = load_org(org_dir, env="local")
    calls = []

    class FakeResolver(SourceResolver):
        def _fetch_sharepoint(self, ref):
            calls.append(ref.path)
            return "Supplier ACME is exempt from PO matching until 30 June."

    build = compile_agent(org, "ap-clerk", FakeResolver(org.root))
    assert calls == ["Agents/exceptions.md"]
    assert "ACME is exempt" in build.instructions
    assert "sharepoint:contoso.sharepoint.com/sites/Finance/Agents/exceptions.md" in build.instruction_sources

    offline = compile_agent(org, "ap-clerk", SourceResolver(org.root, offline=True))
    assert "fetched at deploy time" in offline.instructions
