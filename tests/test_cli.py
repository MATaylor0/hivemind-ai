from __future__ import annotations

import json

from hivemind.cli import main
from hivemind.loader import load_org


def test_validate_and_tree(org_dir, capsys):
    assert main(["--org", str(org_dir), "validate"]) == 0
    assert "0 errors" in capsys.readouterr().out
    assert main(["--org", str(org_dir), "tree", "--format", "mermaid"]) == 0
    assert "chief_of_staff --> finance_manager" in capsys.readouterr().out


def test_render_json(org_dir, capsys):
    assert main(["--org", str(org_dir), "render", "ap-clerk", "-e", "local", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["definition"]["kind"] == "prompt"


def test_new_agent_scaffold_validates(org_dir, capsys):
    code = main(
        [
            "--org",
            str(org_dir),
            "new",
            "treasury-analyst",
            "--role",
            "worker",
            "--team",
            "finance",
            "--reports-to",
            "finance-manager",
            "--owner",
            "treasury@contoso.com",
            "--description",
            "Forecasts cash and manages bank balances.",
        ]
    )
    assert code == 0
    org = load_org(org_dir)
    assert org.agents["treasury-analyst"].reports_to == "finance-manager"
    assert main(["--org", str(org_dir), "validate"]) == 0


def test_plan_apply_local(org_dir, capsys):
    assert main(["--org", str(org_dir), "apply", "-e", "local", "--yes"]) == 0
    assert main(["--org", str(org_dir), "plan", "-e", "local", "--detailed-exitcode"]) == 0
    assert "6 unchanged" in capsys.readouterr().out
    assert main(["--org", str(org_dir), "inventory", "-e", "local", "--format", "markdown"]) == 0
    assert "deployed v1" in capsys.readouterr().out


def test_apply_creates_shared_memory_store(org_dir):
    assert main(["--org", str(org_dir), "apply", "-e", "local", "--yes"]) == 0
    state = json.loads((org_dir / ".hivemind" / "local" / "state.json").read_text())
    assert "hivemind-memory" in state["memory_stores"]
