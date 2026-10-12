from __future__ import annotations

import asyncio
import json

import pytest
import yaml

from hivemind.azd import build
from hivemind.instructions import SourceResolver
from hivemind.loader import load_org
from hivemind.policy import check_org
from hivemind.processes import load_process, rename_agents

from .azd_schema import errors as schema_errors
from .conftest import edit_yaml


@pytest.fixture
def dev(org_dir, monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENDPOINT_DEV", "https://contoso-dev.services.ai.azure.com/api/projects/hivemind")
    return load_org(org_dir, env="dev")


def generate(org, tmp_path):
    return build(org, tmp_path / "build", SourceResolver(org.root, offline=True), git_sha="abc123")


def test_generated_azure_yaml_is_valid_for_azd(dev, tmp_path):
    result = generate(dev, tmp_path)
    document = yaml.safe_load(result.azure_yaml.read_text())
    assert schema_errors(document) == []
    services = document["services"]
    assert services["foundry"]["endpoint"].endswith("/api/projects/hivemind")
    assert [d["name"] for d in services["foundry"]["deployments"]] == ["gpt-5-mini", "text-embedding-3-small"]


def test_agents_become_prompt_agent_services(dev, tmp_path):
    services = yaml.safe_load(generate(dev, tmp_path).azure_yaml.read_text())["services"]
    ap = services["ap-clerk"]
    assert ap["host"] == "azure.ai.agent" and ap["kind"] == "prompt" and ap["name"] == "dev-ap-clerk"
    assert ap["model"] == "gpt-5-mini" and "three-way match" in ap["instructions"]
    assert ap["memory"] == {
        "store": "dev-ap-clerk-memory",
        "chatModel": "gpt-5-mini",
        "embeddingModel": "text-embedding-3-small",
        "scope": "ap-clerk",
    }
    assert all(t["type"] != "memory_search_preview" for t in ap["tools"])
    assert ap["metadata"]["hm_autonomy"] == "approve" and ap["metadata"]["hm_git_sha"] == "abc123"
    routine = services["ap-clerk-morning-invoice-sweep"]
    assert routine["host"] == "azure.ai.routine" and routine["uses"] == ["ap-clerk"]
    assert routine["action"] == {
        "type": "invoke_agent_responses_api",
        "agentName": "ap-clerk",
        "input": "Process all invoices received since the last sweep. Report what was "
        "coded, held and queued for approval.",
    }


def test_eval_services_and_datasets(dev, tmp_path):
    result = generate(dev, tmp_path)
    services = result.document["services"]
    evals = services["ap-clerk-evals"]
    assert evals["host"] == "azure.ai.eval" and evals["uses"] == ["foundry", "ap-clerk"]
    assert (result.root / "evals" / "datasets" / "ap-clerk.jsonl").is_file()
    assert "datasets" not in services["policy-assistant-evals"]  # shadow pilot: traces only


def test_process_becomes_hosted_agent_with_host_project(dev, tmp_path):
    result = generate(dev, tmp_path)
    service = result.document["services"]["process-invoice-to-pay"]
    assert service["kind"] == "hosted" and service["name"] == "dev-process-invoice-to-pay"
    assert service["uses"] == ["foundry", "ap-clerk", "compliance-reviewer"]
    assert service["env"]["FOUNDRY_PROJECT_ENDPOINT"] == "$${{project.endpoint}}"
    project = result.root / "processes" / "invoice-to-pay"
    workflow = yaml.safe_load((project / "workflow.yaml").read_text())
    names = [a["agent"]["name"] for a in workflow["trigger"]["actions"] if a["kind"] == "InvokeAzureAgent"]
    assert names == ["dev-ap-clerk", "dev-compliance-reviewer", "dev-ap-clerk"]
    assert (project / "main.py").read_text().startswith('"""HiveMind process host')
    assert "dotnet-install.sh" in (project / "Dockerfile").read_text()
    trigger = result.document["services"]["process-invoice-to-pay-weekday-run"]
    assert trigger["action"]["type"] == "invoke_agent_invocations_api"


def test_rebuild_keeps_azd_environment_state(dev, tmp_path):
    result = generate(dev, tmp_path)
    state = result.root / ".azure" / "dev" / ".env"
    state.parent.mkdir(parents=True)
    state.write_text("AZURE_LOCATION=australiaeast\n")
    generate(dev, tmp_path)
    assert state.read_text() == "AZURE_LOCATION=australiaeast\n"


def test_build_manifest(dev, tmp_path):
    manifest = json.loads((generate(dev, tmp_path).root / "hivemind-build.json").read_text())
    assert manifest["agents"]["ap-clerk"]["autonomy"] == "approve" and manifest["processes"] == ["invoice-to-pay"]


# ------------------------------------------------------------------ process validation


def test_process_steps(org):
    info = load_process(org, org.processes["invoice-to-pay"])
    assert info.agents == ["ap-clerk", "compliance-reviewer"]
    assert [s.kind for s in info.human_steps] == ["RequestExternalInput"]


def test_supervised_agents_need_a_human_step(org_dir):
    path = org_dir / "processes" / "invoice-to-pay.workflow.yaml"
    workflow = yaml.safe_load(path.read_text())
    workflow["trigger"]["actions"] = [a for a in workflow["trigger"]["actions"] if a["kind"] != "RequestExternalInput"]
    path.write_text(yaml.safe_dump(workflow))
    findings = [f.message for f in check_org(load_org(org_dir)) if f.subject == "process:invoice-to-pay"]
    assert any("needs a human step" in m for m in findings)


def test_unknown_and_shadow_agents_in_processes(org_dir):
    path = org_dir / "processes" / "invoice-to-pay.workflow.yaml"
    workflow = yaml.safe_load(path.read_text())
    workflow["trigger"]["actions"].insert(0, {"kind": "InvokeAzureAgent", "id": "x", "agent": {"name": "ghost"}})
    workflow["trigger"]["actions"].insert(0, {"kind": "InvokeAzureAgent", "id": "y", "agentName": "policy-assistant"})
    path.write_text(yaml.safe_dump(workflow))
    findings = [(f.level, f.message) for f in check_org(load_org(org_dir)) if f.subject == "process:invoice-to-pay"]
    assert ("error", "step 'x': unknown agent 'ghost'") in findings
    assert any(level == "warning" and "shadow mode" in m for level, m in findings)


def test_process_spec_errors(org_dir):
    edit_yaml(org_dir / "processes" / "invoice-to-pay.yaml", workflow="processes/missing.yaml", owner=None)
    findings = [f.message for f in check_org(load_org(org_dir)) if f.subject == "process:invoice-to-pay"]
    assert any("workflow file not found" in m for m in findings)


def test_rename_handles_every_agent_reference_form():
    workflow = {
        "kind": "Workflow",
        "trigger": {
            "actions": [
                {"kind": "InvokeAzureAgent", "agent": {"name": "a"}},
                {"kind": "InvokeAzureAgent", "agentName": "b"},
                {"kind": "If", "condition": "=true", "actions": [{"kind": "InvokeAzureAgent", "agent": "c"}]},
            ]
        },
    }
    renamed = rename_agents(workflow, lambda n: f"dev-{n}")
    actions = renamed["trigger"]["actions"]
    assert actions[0]["agent"]["name"] == "dev-a" and actions[1]["agentName"] == "dev-b"
    assert actions[2]["actions"][0]["agent"] == "dev-c"
    assert workflow["trigger"]["actions"][0]["agent"]["name"] == "a"  # original untouched


# ------------------------------------------------------------------ running a process (Agent Framework)


def test_local_process_run_pauses_for_a_person_and_resumes(org):
    pytest.importorskip("agent_framework_declarative")
    from hivemind.processes import LocalProcessRun

    calls = []

    def invoke(agent, text):
        calls.append(agent)
        return {"ap-clerk": "Coded 3 invoices; INV-7 on hold.", "compliance-reviewer": "APPROVED"}[agent]

    run = LocalProcessRun(org, "invoice-to-pay", invoke)
    paused = asyncio.run(run.run("Process today's invoices"))
    assert paused.status == "awaiting_input" and calls == ["ap-clerk", "compliance-reviewer"]
    assert "Reply 'approved'" in paused.requests[0]["prompt"]

    run = LocalProcessRun(org, "invoice-to-pay", invoke)
    calls.clear()
    done = asyncio.run(run.run("Process today's invoices", answer=lambda prompt: "approved"))
    assert done.status == "completed" and calls == ["ap-clerk", "compliance-reviewer", "ap-clerk"]
    assert done.outputs[-1] == "Coded 3 invoices; INV-7 on hold."


def test_cli_process_run(org_dir, capsys):
    pytest.importorskip("agent_framework_declarative")
    from hivemind.cli import main

    base = ["--org", str(org_dir)]
    assert main([*base, "apply", "-e", "local", "--yes"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                *base,
                "process",
                "run",
                "invoice-to-pay",
                "Process today's invoices",
                "--answer",
                "approved",
                "-e",
                "local",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "[local:ap-clerk] received:" in out and "[completed]" in out
