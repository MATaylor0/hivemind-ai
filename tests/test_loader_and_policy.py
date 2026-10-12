from __future__ import annotations

import pytest

from hivemind.loader import LoadError, load_org
from hivemind.policy import check_org, has_errors

from .conftest import EXAMPLE, edit_yaml


def errors(org):
    return [f for f in check_org(org) if f.level == "error"]


def test_example_org_is_clean():
    org = load_org(EXAMPLE, env="local")
    assert len(org.agents) == 6
    assert check_org(org) == []


def test_variables_resolve_per_environment():
    org = load_org(EXAMPLE, env="local")
    assert org.tools["erp-invoices"].server_url == "https://localhost/erp-mcp"
    unresolved = load_org(EXAMPLE)  # no env: placeholders are kept, not errors
    assert unresolved.tools["erp-invoices"].server_url == "${var:ERP_MCP_URL}"


def test_missing_env_variable_is_an_error_for_that_env(monkeypatch):
    monkeypatch.delenv("FOUNDRY_ENDPOINT_PROD", raising=False)
    with pytest.raises(LoadError, match="FOUNDRY_ENDPOINT_PROD"):
        load_org(EXAMPLE, env="prod")
    monkeypatch.setenv("FOUNDRY_ENDPOINT_PROD", "https://contoso.services.ai.azure.com/api/projects/prod")
    org = load_org(EXAMPLE, env="prod")
    assert org.env.endpoint.endswith("/prod")


def test_unknown_environment():
    with pytest.raises(LoadError, match="unknown environment"):
        load_org(EXAMPLE, env="staging")


def test_schema_errors_are_reported_with_file(org_dir):
    (org_dir / "agents" / "bad.yaml").write_text("name: Not Valid\nrole: worker\n")
    with pytest.raises(LoadError) as exc:
        load_org(org_dir)
    message = str(exc.value)
    assert "bad.yaml" in message and "description" in message and "kebab-case" in message


def test_duplicate_agent_names(org_dir):
    src = org_dir / "agents" / "finance" / "ap-clerk.yaml"
    (org_dir / "agents" / "copy.yaml").write_text(src.read_text())
    with pytest.raises(LoadError, match="duplicate AgentSpec 'ap-clerk'"):
        load_org(org_dir)


def test_reporting_cycle_is_an_error(org_dir):
    edit_yaml(org_dir / "agents" / "executive" / "chief-of-staff.yaml", reports_to="ap-clerk")
    findings = errors(load_org(org_dir))
    assert any("reporting cycle" in f.message for f in findings)


def test_unknown_references(org_dir):
    edit_yaml(org_dir / "agents" / "finance" / "fpa-analyst.yaml", reports_to="ghost", tools=["nope"])
    messages = [f.message for f in errors(load_org(org_dir))]
    assert any("reports_to 'ghost'" in m for m in messages)
    assert any("unknown tool/knowledge 'nope'" in m for m in messages)


def test_tool_audience_is_enforced(org_dir):
    # erp-invoices is restricted to ap-clerk and finance-manager.
    edit_yaml(org_dir / "agents" / "finance" / "fpa-analyst.yaml", tools=["erp-invoices"])
    messages = [f.message for f in errors(load_org(org_dir))]
    assert any("not in the audience of 'erp-invoices'" in m for m in messages)


def test_write_tools_need_reviewer_or_approval(org_dir):
    edit_yaml(org_dir / "agents" / "finance" / "ap-clerk.yaml", reviewed_by=None)
    tool = org_dir / "catalog" / "tools" / "erp-invoices.yaml"
    edit_yaml(tool, require_approval="never")
    messages = [f.message for f in errors(load_org(org_dir))]
    assert any("write tool 'erp-invoices' needs a reviewer" in m for m in messages)

    edit_yaml(tool, require_approval="always")
    assert not has_errors(check_org(load_org(org_dir)))


def test_model_allow_list(org_dir):
    edit_yaml(org_dir / "agents" / "finance" / "fpa-analyst.yaml", model="gpt-3.5-turbo")
    assert any("not in the allowed list" in f.message for f in errors(load_org(org_dir)))


def test_owner_and_budget_required(org_dir):
    edit_yaml(org_dir / "agents" / "finance" / "fpa-analyst.yaml", owner=None, budget=None)
    messages = [f.message for f in errors(load_org(org_dir))]
    assert any("owner is required" in m for m in messages)
    assert any("budget.monthly_usd is required" in m for m in messages)


def test_memory_scope_opt_in_requires_permission(org_dir):
    edit_yaml(org_dir / "agents" / "people" / "policy-assistant.yaml", memory={"shared": ["month-end-close"]})
    assert any("not permitted to use memory scope" in f.message for f in errors(load_org(org_dir)))


def test_shared_user_memory_warning(org_dir):
    edit_yaml(
        org_dir / "agents" / "people" / "policy-assistant.yaml",
        memory={"private": True, "private_scope": "agent"},
    )
    warnings = [f for f in check_org(load_org(org_dir)) if f.level == "warning"]
    assert any("shared across all users" in f.message for f in warnings)


def test_hub_url_required_for_foundry_env(monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENDPOINT_DEV", "https://x/api/projects/dev")
    org = load_org(EXAMPLE, env="dev")
    assert not has_errors(check_org(org))  # default hub URL supplied via ${env:...:-default}
    org.config.environments["dev"].hub_url = None
    assert any("no hub URL" in f.message for f in errors(org))


def test_user_context_tools_warn_for_unattended_or_unpublished_agents(org_dir):
    path = org_dir / "agents" / "executive" / "chief-of-staff.yaml"
    edit_yaml(path, schedules=[{"name": "daily", "cron": "0 7 * * 1-5", "input": "Morning briefing"}])
    warnings = [f.message for f in check_org(load_org(org_dir)) if f.subject == "agent:chief-of-staff"]
    assert any("no signed-in person during scheduled runs" in w for w in warnings)

    edit_yaml(path, schedules=None, channels={"m365": {"enabled": False}})
    warnings = [f.message for f in check_org(load_org(org_dir)) if f.subject == "agent:chief-of-staff"]
    assert any("need a signed-in person" in w and "policy-library" in w for w in warnings)


def test_sharepoint_knowledge_is_user_context_by_default():
    org = load_org(EXAMPLE)
    assert org.knowledge["policy-library"].as_tool().user_context
    assert not org.knowledge["org-policies"].as_tool().user_context
