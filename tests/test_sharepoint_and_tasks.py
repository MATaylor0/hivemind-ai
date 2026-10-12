from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime

import pytest

from hivemind.compiler import compile_agent
from hivemind.docx import docx_to_markdown, markdown_to_docx
from hivemind.graph import GraphClient, GraphError, PreconditionFailed
from hivemind.instructions import SourceResolver
from hivemind.loader import load_org
from hivemind.policy import check_org
from hivemind.sharepoint import GUIDE_NAME, provision
from hivemind.tasks import SharePointTaskStore, Task
from hivemind.workspace import workspace_for

from .conftest import edit_yaml

# ------------------------------------------------------------------ Word documents


def test_docx_roundtrip():
    md = "# Supplier rules\n\nIntro with **bold** text.\n\n## Exceptions\n\n- ACME: no GRN under $500\n- Northwind"
    back = docx_to_markdown(markdown_to_docx(md))
    assert "# Supplier rules" in back and "## Exceptions" in back
    assert "- ACME: no GRN under $500" in back and "**bold**" in back


# ------------------------------------------------------------------ rules layering


def test_sharepoint_rules_are_added_to_matching_layers(org):
    build = compile_agent(org, "ap-clerk", SourceResolver(org.root, workspace=workspace_for(org)))
    text = build.instructions
    assert "Fabrikam Consulting" in text  # Agents/ap-clerk/Supplier exceptions.docx
    assert "AP cut-off at 12:00" in text  # Teams/finance/01-Month-end calendar.md
    assert text.index("AP cut-off at 12:00") < text.index("## Your job") < text.index("Fabrikam")
    assert "local:Agents/ap-clerk/Supplier exceptions.docx" in build.instruction_sources
    assert "How to use" not in text  # guide documents are ignored

    # policy-assistant is in another team and gets neither
    other = compile_agent(org, "policy-assistant", SourceResolver(org.root, workspace=workspace_for(org))).instructions
    assert "Fabrikam" not in other and "AP cut-off" not in other


def test_editing_a_rule_document_changes_the_agent_version(org_dir):
    org = load_org(org_dir, env="local")
    before = compile_agent(org, "ap-clerk", SourceResolver(org.root, workspace=workspace_for(org))).content_hash
    doc = org_dir / "sharepoint" / "Agent Rules" / "Agents" / "ap-clerk" / "02-New rule.md"
    doc.write_text("Never pay a supplier on a weekend.")
    after = compile_agent(org, "ap-clerk", SourceResolver(org.root, workspace=workspace_for(org)))
    assert after.content_hash != before and "Never pay a supplier on a weekend." in after.instructions


def test_goals_section(org):
    text = compile_agent(org, "finance-manager").instructions
    assert "- Close the books within 3 business days → Lift FY27 operating margin to 18%" in text


def test_goal_policy(org_dir):
    edit_yaml(org_dir / "agents" / "finance" / "fpa-analyst.yaml", goals=["world-peace"])
    assert any("unknown goal 'world-peace'" in f.message for f in check_org(load_org(org_dir)))


# ------------------------------------------------------------------ provisioning


def test_local_provisioning_creates_folders_and_guides(org_dir):
    org = load_org(org_dir, env="local")
    provision(org, log=lambda _: None)
    rules = org_dir / "sharepoint" / "Agent Rules"
    for folder in ["Organisation", "Roles/manager", "Teams/finance", "Agents/fpa-analyst"]:
        assert (rules / folder / GUIDE_NAME).is_file()
    assert (org_dir / "sharepoint" / "Agent Outputs" / "finance" / "ap-clerk").is_dir()


class FakeGraph(GraphClient):
    """Emulates the slice of Graph used by HiveMind: sites, lists, list items with ETags, drives."""

    def __init__(self):
        super().__init__(token=lambda: "t")
        self.lists: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self._ids = itertools.count(1)

    def request(self, method, path, body=None, headers=None, raw=False):
        self.calls.append((method, path))
        headers = headers or {}
        if path.startswith("/sites/contoso.sharepoint.com:"):
            return {"id": "site1"}
        if path == "/sites/site1/lists" and method == "GET":
            return {"value": [{"displayName": n, "id": n} for n in self.lists]}
        if path == "/sites/site1/lists" and method == "POST":
            self.lists[body["displayName"]] = body
            return {"id": body["displayName"]}
        if path.startswith("/sites/site1/lists/") and path.count("/") == 4 and method == "GET":
            name = path.rsplit("/", 1)[1].replace("%20", " ")
            return {"id": name}
        if path == "/sites/site1/drives":
            return {"value": [{"name": n, "id": n} for n in self.lists]}
        if path.startswith("/drives/"):
            if method == "PUT":
                self.files[path] = body
                return {"id": "f", "name": path, "webUrl": "https://sp/" + path}
            if method == "GET" and path.endswith(":/content"):
                if path in {p.replace(":/content", ":/content") for p in self.files}:
                    return self.files[path]
                raise GraphError(404, "not found")
            return {}
        if "/items" in path:
            return self._items(method, path, body, headers)
        raise AssertionError(f"unexpected call {method} {path}")

    def _items(self, method, path, body, headers):
        tail = path.split("/items", 1)[1]
        if method == "POST" and tail == "":
            item_id = str(next(self._ids))
            self.items[item_id] = {
                "id": item_id,
                "eTag": f'"{item_id},1"',
                "fields": dict(body["fields"]),
                "createdDateTime": datetime.now(UTC).isoformat(),
                "lastModifiedDateTime": datetime.now(UTC).isoformat(),
            }
            return self.items[item_id]
        if method == "GET" and tail.startswith("?"):
            return {"value": list(self.items.values())}
        item_id = tail.strip("/").split("/")[0].split("?")[0]
        item = self.items[item_id]
        if method == "GET":
            return item
        if method == "PATCH":
            if "If-Match" in headers and headers["If-Match"] != item["eTag"]:
                raise PreconditionFailed(412, "etag mismatch")
            item["fields"].update(body)
            version = int(item["eTag"].strip('"').split(",")[1]) + 1
            item["eTag"] = f'"{item_id},{version}"'
            return item["fields"]
        raise AssertionError(f"unexpected {method} {path}")


def test_sharepoint_task_store_round_trip_and_concurrency():
    graph = FakeGraph()
    store = SharePointTaskStore(graph, "contoso.sharepoint.com/sites/HiveMind", "Agent Tasks")
    created = store.create(
        Task(
            title="Process invoice 42",
            instructions="Process it",
            assigned_to="ap-clerk",
            requested_by="finance-manager",
            goal="working-capital",
        )
    )
    assert created.id == "T-1" and created.root_id == "T-1"
    fields = graph.items["1"]["fields"]
    assert fields["AssignedAgent"] == "ap-clerk" and fields["Status"] == "Queued" and fields["RootTask"] == "T-1"

    fresh = store.get("T-1")
    won = store.transition(fresh, status="InProgress", attempts=1)
    assert won is not None and won.status == "InProgress"
    assert store.transition(fresh, status="InProgress") is None  # stale ETag: another replica already claimed

    store.update(won, status="Done", result="Coded to 6120")
    assert store.query(status="Done")[0].result == "Coded to 6120"
    assert store.query(status="Queued") == []


def test_human_created_list_items_become_person_tasks():
    graph = FakeGraph()
    graph.items["7"] = {
        "id": "7",
        "eTag": '"7,1"',
        "fields": {"Title": "Why did freight costs jump in March?", "AssignedAgent": "fpa-analyst", "Status": "Queued"},
        "createdBy": {"user": {"email": "cfo@contoso.com"}},
        "createdDateTime": "2026-10-01T00:00:00Z",
        "lastModifiedDateTime": "2026-10-01T00:00:00Z",
    }
    task = SharePointTaskStore(graph, "contoso.sharepoint.com/sites/HiveMind", "Agent Tasks").get("T-7")
    assert task.requester_kind == "person" and task.requested_by == "cfo@contoso.com"
    assert task.instructions == "Why did freight costs jump in March?" and task.depth == 0 and task.root_id == "T-7"


def test_graph_provisioning_creates_libraries_list_and_agent_dropdown(org, monkeypatch):
    graph = FakeGraph()
    org.config.environments["local"].sharepoint_local_dir = None
    provision(org, log=lambda _: None, graph=graph)
    assert {"Agent Rules", "Agent Outputs", "Agent Tasks"} <= set(graph.lists)
    columns = {c["name"]: c for c in graph.lists["Agent Tasks"]["columns"]}
    assert columns["AssignedAgent"]["choice"]["choices"] == sorted(org.active_agents())
    assert columns["Status"]["indexed"] is True
    assert any(GUIDE_NAME.replace(" ", "%20") in p for p in graph.files)


def test_cli_task_flow(org_dir, capsys):
    from hivemind.cli import main

    base = ["--org", str(org_dir)]
    assert main([*base, "apply", "-e", "local", "--yes"]) == 0
    assert (
        main(
            [
                *base,
                "tasks",
                "add",
                "fpa-analyst",
                "Explain the March freight variance",
                "--goal",
                "fy27-margin",
                "-e",
                "local",
            ]
        )
        == 0
    )
    assert main([*base, "tasks", "run", "-e", "local"]) == 0
    capsys.readouterr()
    assert main([*base, "tasks", "list", "-e", "local", "--json"]) == 0
    tasks = json.loads(capsys.readouterr().out)
    assert tasks[0]["status"] == "Done" and "[local:fpa-analyst] received" in tasks[0]["result"]


def test_pause_blocks_work(org_dir, capsys):
    from hivemind.cli import main

    base = ["--org", str(org_dir)]
    main([*base, "apply", "-e", "local", "--yes"])
    assert main([*base, "pause", "ap-clerk", "-e", "local"]) == 0
    main([*base, "tasks", "add", "ap-clerk", "Process invoice 42", "-e", "local"])
    main([*base, "tasks", "run", "-e", "local"])
    capsys.readouterr()
    main([*base, "tasks", "list", "-e", "local", "--json"])
    task = json.loads(capsys.readouterr().out)[0]
    assert task["status"] == "Failed" and "paused" in task["error"]


@pytest.mark.parametrize("missing", ["audience", "connection"])
def test_entra_hub_config_policy(org_dir, monkeypatch, missing):
    monkeypatch.setenv("FOUNDRY_ENDPOINT_DEV", "https://x/api/projects/dev")
    org = load_org(org_dir, env="dev")
    setattr(org.config.hub, missing, None)
    assert any(f"hub.{missing}" in f.message or missing in f.message for f in check_org(org) if f.level == "error")
