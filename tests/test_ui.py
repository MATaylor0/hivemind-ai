from __future__ import annotations

import http.client
import json
import threading

import pytest

from hivemind.ui import data
from hivemind.ui.server import ConsoleServer, check_command

PROTECTED = {"prod"}


@pytest.mark.parametrize(
    ("line", "changes"),
    [
        ("validate", False),
        ("hivemind tree --format mermaid", False),
        ("plan -e prod", False),
        ("inventory -e prod --format json", False),
        ("gate -e prod", False),
        ("build -e prod --offline", False),
        ("process list", False),
        ("apply -e local", True),
        ("process run invoice-to-pay 'go' -e local", True),
        ("evals add ap-clerk --query hi", True),
        ("sharepoint provision -e dev", True),
        ("pause ap-clerk -e dev", True),
    ],
)
def test_allowed_commands(line, changes):
    check = check_command(line, PROTECTED)
    assert check.error is None and check.changes is changes
    assert bool(check.confirm_message) is changes


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("apply -e prod", "protected environment"),
        ("publish chief-of-staff --env=prod", "protected environment"),
        ("pause ap-clerk -e prod", "protected environment"),
        ("ls -la; rm -rf /", "not a hivemind command"),
        ("ui", "already running"),
        ("validate --org /etc", "--org is not allowed"),
        ("validate 'unclosed", "could not parse"),
        ("", "type a hivemind command"),
    ],
)
def test_refused_commands(line, message):
    assert message in (check_command(line, PROTECTED).error or "")


def test_apply_gets_yes_because_the_console_has_no_stdin():
    assert check_command("apply -e local", PROTECTED).args == ["apply", "-e", "local", "--yes"]


def test_overview_and_agent_detail(org_dir):
    overview = data.overview(org_dir, "local")
    assert overview["counts"]["agents"] == 6 and overview["counts"]["errors"] == 0
    assert overview["chart"][0]["name"] == "chief-of-staff"
    assert {g["id"] for g in overview["goals"]} == {"fy27-margin", "employee-experience"}
    detail = data.agent_detail(org_dir, "local", "ap-clerk")
    assert "Fabrikam" in detail["build"]["instructions"]
    assert any(t["id"] == "erp-invoices" and t["access"] == "write" for t in detail["tools"])


def test_overview_keeps_environments_when_an_environment_cannot_load(org_dir, monkeypatch):
    monkeypatch.delenv("FOUNDRY_ENDPOINT_PROD", raising=False)
    overview = data.overview(org_dir, "prod")
    assert "FOUNDRY_ENDPOINT_PROD" in overview["error"]
    assert {e["name"] for e in overview["environments"]} == {"local", "dev", "prod"}


@pytest.fixture
def server(org_dir):
    srv = ConsoleServer(0, org_dir, "local", "tok")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    all_headers = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    payload = json.dumps(body).encode() if body is not None else None
    if payload:
        all_headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=all_headers)
    response = conn.getresponse()
    return response.status, dict(response.getheaders()), response.read().decode()


def test_token_cookie_and_host_checks(server):
    status, headers, _ = request(server, "GET", "/?token=tok")
    assert status == 302 and "hm_token=tok" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    assert request(server, "GET", "/api/overview")[0] == 401
    assert request(server, "GET", "/api/overview", headers={"Cookie": "hm_token=wrong"})[0] == 401
    status, headers, _ = request(server, "GET", "/", headers={"Cookie": "hm_token=tok"})
    assert status == 200 and "script-src 'self'" in headers["Content-Security-Policy"]
    assert request(server, "GET", "/api/overview", headers={"Cookie": "hm_token=tok", "Host": "evil.example"})[0] == 403


def test_run_streams_output_and_requires_confirmation(server):
    auth = {"Cookie": "hm_token=tok"}
    status, _, body = request(server, "POST", "/api/run", {"command": "validate"}, auth)
    assert status == 200 and "0 errors" in body
    assert json.loads(body.split("\x1e")[1])["exit"] == 0

    status, _, body = request(server, "POST", "/api/run", {"command": "apply -e local"}, auth)
    assert status == 409 and json.loads(body)["needs_confirmation"]
    status, _, body = request(server, "POST", "/api/run", {"command": "apply -e local", "confirm": True}, auth)
    assert status == 200 and "6 to create" in body

    status, _, body = request(server, "POST", "/api/run", {"command": "apply -e prod", "confirm": True}, auth)
    assert status == 403 and "protected" in json.loads(body)["error"]
