from __future__ import annotations

import itertools
from datetime import UTC, datetime

from hivemind.compiler import compile_agent
from hivemind.docx import docx_to_markdown, markdown_to_docx
from hivemind.graph import GraphClient, GraphError, PreconditionFailed  # noqa: F401
from hivemind.instructions import SourceResolver
from hivemind.loader import load_org
from hivemind.policy import check_org
from hivemind.sharepoint import GUIDE_NAME, provision
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


def test_graph_provisioning_creates_rules_library_and_folders(org, monkeypatch):
    graph = FakeGraph()
    org.config.environments["local"].sharepoint_local_dir = None
    provision(org, log=lambda _: None, graph=graph)
    assert set(graph.lists) == {"Agent Rules"}
    assert any(GUIDE_NAME.replace(" ", "%20") in p for p in graph.files)
    assert any("Agents/ap-clerk" in p for p in graph.files)
