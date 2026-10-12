from __future__ import annotations

import json

from hivemind.compiler import compile_agent
from hivemind.evals import add_case, dataset_rows, eval_service, local_gate, pass_rate
from hivemind.loader import load_org
from hivemind.policy import check_org
from hivemind.schema import AutonomyLevel

from .conftest import edit_yaml

AP = ("agents", "finance", "ap-clerk.yaml")


def mcp_tools(build):
    return {t["server_label"]: t for t in build.definition["tools"] if t["type"] == "mcp"}


def set_level(org_dir, level, **extra):
    rows = "\n".join(json.dumps({"query": f"case {i}"}) for i in range(30))
    (org_dir / "evals" / "ap-clerk.jsonl").write_text(rows)
    edit_yaml(org_dir.joinpath(*AP), autonomy={"level": level, "approved_by": "ap.lead@contoso.com", **extra})
    return load_org(org_dir, env="local")


def test_shadow_and_suggest_remove_write_tools(org_dir):
    for level in ("shadow", "suggest"):
        build = compile_agent(set_level(org_dir, level), "ap-clerk")
        assert "erp_invoices" not in mcp_tools(build)
        assert "fabric_finance_warehouse" in mcp_tools(build)  # read tools stay
        assert build.removed_tools == ["erp-invoices"]
        assert f"**{level} mode**" in build.instructions
        assert "Tools held back at this level: `erp-invoices`" in build.instructions
        assert build.metadata["hm_autonomy"] == level


def test_approve_gates_every_mcp_write(org_dir):
    build = compile_agent(set_level(org_dir, "approve"), "ap-clerk")
    assert mcp_tools(build)["erp_invoices"]["require_approval"] == "always"
    assert build.gated_tools == ["erp-invoices"]
    assert "**act with approval**" in build.instructions


def test_autonomous_keeps_catalogue_approvals(org_dir):
    build = compile_agent(set_level(org_dir, "autonomous"), "ap-clerk")
    assert mcp_tools(build)["erp_invoices"]["require_approval"] == {
        "always": {"tool_names": ["approve_for_payment"]},
        "never": {"tool_names": ["get_invoice", "search_invoices", "code_invoice", "place_on_hold"]},
    }
    sampled = compile_agent(set_level(org_dir, "autonomous_sampled", sample_rate=0.2), "ap-clerk")
    assert sampled.metadata["hm_sample_rate"] == "0.2"


def test_non_approvable_write_tools_are_held_at_approve(org_dir):
    (org_dir / "catalog" / "tools" / "erp-api.yaml").write_text(
        "id: erp-api\nkind: raw\naccess: write\ndefinition: {type: function, name: post_journal}\n"
        'audience: ["agent:ap-clerk"]\n'
    )
    edit_yaml(org_dir.joinpath(*AP), tools=["erp-invoices", "fabric-finance-warehouse", "erp-api"])
    build = compile_agent(load_org(org_dir, env="local"), "ap-clerk")
    assert "erp-api" in build.removed_tools and "erp-invoices" in build.gated_tools


def test_environment_caps_autonomy(org_dir, monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENDPOINT_PROD", "https://x/api/projects/prod")
    set_level(org_dir, "autonomous")
    org = load_org(org_dir, env="prod")  # prod: max_autonomy approve
    build = compile_agent(org, "ap-clerk")
    assert build.autonomy == AutonomyLevel.APPROVE
    assert any("capped to 'approve'" in f.message for f in check_org(org))


def test_promotion_needs_evidence(org_dir):
    path = org_dir.joinpath(*AP)
    edit_yaml(path, autonomy={"level": "autonomous"})
    messages = [f.message for f in check_org(load_org(org_dir)) if f.subject == "agent:ap-clerk"]
    assert any("needs autonomy.approved_by" in m for m in messages)
    assert any("at least 30 cases (evals.dataset has 10)" in m for m in messages)

    edit_yaml(path, autonomy={"level": "autonomous_sampled", "approved_by": "x@contoso.com"})
    messages = [f.message for f in check_org(load_org(org_dir)) if f.subject == "agent:ap-clerk"]
    assert any("needs autonomy.sample_rate" in m for m in messages)


def test_new_agents_default_to_shadow(org):
    assert org.agents["policy-assistant"].autonomy.level == AutonomyLevel.SHADOW


def test_dataset_validation(org_dir):
    (org_dir / "evals" / "ap-clerk.jsonl").write_text('{"query": "ok"}\n{"expected": "no query"}\n')
    messages = [f.message for f in check_org(load_org(org_dir))]
    assert any("ap-clerk.jsonl:2: each row needs a non-empty 'query'" in m for m in messages)


def test_add_case_appends_golden_row(org):
    agent = org.agents["ap-clerk"]
    before = len(dataset_rows(org, agent))
    add_case(org, agent, "Invoice INV-9 has a handwritten PO number", "Places it on hold", note="AP correction")
    rows = dataset_rows(org, agent)
    assert len(rows) == before + 1 and rows[-1]["note"] == "AP correction"


def test_gate_threshold_follows_level_or_override(org_dir):
    org = load_org(org_dir)
    assert pass_rate(org, org.agents["ap-clerk"]) == 0.8  # approve
    assert pass_rate(org, org.agents["finance-manager"]) == 0.7  # suggest
    edit_yaml(org_dir.joinpath(*AP), evals={"dataset": "evals/ap-clerk.jsonl", "pass_rate": 0.9})
    assert pass_rate(load_org(org_dir), load_org(org_dir).agents["ap-clerk"]) == 0.9


def test_eval_service_shape(org):
    service = eval_service(org, org.agents["ap-clerk"], "ap-clerk", "dev-ap-clerk", "./evals/datasets/ap-clerk.jsonl")
    regression, production = service["evals"]
    assert regression["target"] == {"type": "agent", "name": "ap-clerk"}
    assert regression["evaluators"][0] == {
        "evaluator": "builtin.task_adherence",
        "initializationParameters": {"model": "gpt-5-mini"},
    }
    assert production["source"]["type"] == "traces" and production["source"]["agentName"] == "dev-ap-clerk"
    # an agent without a dataset still gets production-trace evaluation
    shadow = eval_service(org, org.agents["policy-assistant"], "policy-assistant", "policy-assistant", None)
    assert "datasets" not in shadow and shadow["evals"][0]["source"]["type"] == "traces"


def test_local_gate_counts_answered_cases(org):
    passed, total = local_gate(org, org.agents["ap-clerk"], lambda agent, q: f"[{agent}] {q}")
    assert (passed, total) == (10, 10)
    passed, _ = local_gate(org, org.agents["ap-clerk"], lambda agent, q: "")
    assert passed == 0
