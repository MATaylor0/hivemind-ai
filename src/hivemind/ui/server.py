"""``hivemind ui``: a local admin console for the person running HiveMind.

* Serves on 127.0.0.1 only, behind a per-session token (like Jupyter) and a Host-header check.
* Read views (overview, agents, org chart, plan, tasks) call the same code as the CLI.
* The console runs ``hivemind`` sub-commands, never a shell. Commands that change something need a confirmation,
  and are refused against ``protected`` environments: production changes go through a pull request and the
  approval-gated deploy workflow. ``plan`` and other read-only commands work everywhere.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..loader import LoadError, load_org
from . import data

STATIC = Path(__file__).parent / "static"
COMMAND_TIMEOUT_SECONDS = 600
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
STATIC_FILES = {
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
}

# Top-level commands the console may run. `hub serve` is long-running and belongs in a terminal or container.
READ_ONLY = {"validate", "tree", "render", "inventory", "budget", "plan", "identities"}
CHANGES = {"apply", "publish", "invoke", "pause", "resume", "new"}
READ_ONLY_SUB = {("tasks", "list"), ("tasks", "show")}
CHANGES_SUB = {("tasks", "add"), ("tasks", "run"), ("sharepoint", "provision")}


@dataclass
class CommandCheck:
    args: list[str]
    changes: bool
    env: str | None
    error: str | None = None
    confirm_message: str | None = None


def _env_of(args: list[str]) -> str | None:
    for i, arg in enumerate(args):
        if arg in ("-e", "--env") and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--env="):
            return arg.split("=", 1)[1]
    return None


def check_command(line: str, protected_envs: set[str]) -> CommandCheck:
    """Decide whether the console may run ``line``. Pure, so it is easy to test."""
    try:
        args = shlex.split(line)
    except ValueError as exc:
        return CommandCheck([], False, None, error=f"could not parse command: {exc}")
    if args and args[0] == "hivemind":
        args = args[1:]
    if not args:
        return CommandCheck([], False, None, error="type a hivemind command, e.g. validate")
    if any(a == "--org" or a.startswith("--org=") for a in args):
        return CommandCheck(args, False, None, error="the console is bound to its org directory; --org is not allowed")
    if args[0] in ("-h", "--help", "help"):
        return CommandCheck(["--help"], False, None)

    top = args[0]
    pair = (top, args[1]) if len(args) > 1 else (top, "")
    if top in READ_ONLY or pair in READ_ONLY_SUB:
        changes = False
    elif top in CHANGES or pair in CHANGES_SUB:
        changes = True
    elif top in ("tasks", "sharepoint") and (len(args) == 1 or args[1] in ("-h", "--help")):
        changes = False
    elif top == "hub":
        return CommandCheck(
            args,
            False,
            None,
            error="`hub serve` is a long-running server; run it in a terminal or "
            "deploy it to Container Apps (see infra/)",
        )
    else:
        return CommandCheck(args, False, None, error=f"'{top}' is not a hivemind command the console can run")

    env = _env_of(args)
    if changes and env in protected_envs:
        return CommandCheck(
            args,
            True,
            env,
            error=f"'{env}' is a protected environment: changes go through a pull request and the 'Deploy agents' "
            f"workflow. Read-only commands (plan, inventory, tasks list...) are fine.",
        )
    if top == "apply" and "--yes" not in args and "-y" not in args:
        args = [*args, "--yes"]  # the console has no stdin; the confirmation dialog replaces the prompt
    message = None
    if changes:
        target = f" in '{env}'" if env else ""
        message = f"`hivemind {' '.join(args)}` will make changes{target}. Run it?"
    return CommandCheck(args, changes, env, confirm_message=message)


class ConsoleServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, org_dir: Path, default_env: str | None, token: str):
        super().__init__(("127.0.0.1", port), Handler)
        self.org_dir = org_dir
        self.default_env = default_env
        self.token = token

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?token={self.token}"

    def protected_envs(self) -> set[str]:
        try:
            org = load_org(self.org_dir)
        except LoadError:
            return set()
        return {n for n, e in org.config.environments.items() if e.protected}


class Handler(http.server.BaseHTTPRequestHandler):
    server: ConsoleServer
    server_version = "HiveMindConsole/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - quiet by default
        if os.environ.get("HIVEMIND_UI_DEBUG"):
            super().log_message(format, *args)

    # ------------------------------------------------------------------ plumbing

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json")

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _authorised(self) -> bool:
        cookies = dict(c.strip().split("=", 1) for c in self.headers.get("Cookie", "").split(";") if "=" in c)
        supplied = self.headers.get("X-HiveMind-Token") or cookies.get("hm_token", "")
        return secrets.compare_digest(supplied, self.server.token)

    def _guard(self) -> bool:
        if not self._host_ok():
            self._send(403, b"forbidden host", "text/plain")
            return False
        if not self._authorised():
            self._send(401, b"open the console using the URL printed by `hivemind ui`", "text/plain")
            return False
        return True

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if parsed.path == "/":
            if not self._host_ok():
                self._send(403, b"forbidden host", "text/plain")
                return
            token = query.get("token", "")
            if token and secrets.compare_digest(token, self.server.token):
                cookie = f"hm_token={token}; HttpOnly; SameSite=Strict; Path=/"
                self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
                return
            if not self._authorised():
                self._send(401, b"open the console using the URL printed by `hivemind ui`", "text/plain")
                return
            html = (STATIC / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8", {"Content-Security-Policy": CSP})
            return
        if parsed.path in STATIC_FILES:
            name, content_type = STATIC_FILES[parsed.path]
            self._send(200, (STATIC / name).read_bytes(), content_type)
            return
        if not parsed.path.startswith("/api/"):
            self._send(404, b"not found", "text/plain")
            return
        if not self._guard():
            return
        env = query.get("env") or self.server.default_env
        org_dir = self.server.org_dir
        route = parsed.path[len("/api/") :]
        if route == "overview":
            self._json(data.overview(org_dir, env))
        elif route.startswith("agents/"):
            self._json(data.agent_detail(org_dir, env, route.split("/", 1)[1], query.get("fetch") == "1"))
        elif route == "plan":
            if not env:
                self._json({"error": "choose an environment"})
            else:
                self._json(data.plan(org_dir, env))
        elif route == "tasks":
            if not env:
                self._json({"error": "choose an environment"})
            else:
                self._json(data.tasks(org_dir, env))
        elif route == "session":
            self._json({"default_env": self.server.default_env, "org_dir": str(org_dir)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        if self.path != "/api/run":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
            return
        check = check_command(str(body.get("command", "")), self.server.protected_envs())
        if check.error:
            self._json({"error": check.error}, 403 if check.env else 400)
            return
        if check.changes and not body.get("confirm"):
            self._json({"needs_confirmation": True, "message": check.confirm_message}, 409)
            return
        self._stream(check.args)

    def _stream(self, args: list[str]) -> None:
        """Run the command and stream combined output. The last line is ``\\x1e{"exit": N}``."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        command = [sys.executable, "-m", "hivemind.cli", "--org", str(self.server.org_dir), *args]
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "HIVEMIND_CONSOLE": "1"}
        started = time.monotonic()
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed executable, allow-listed sub-commands, no shell
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=self.server.org_dir,
                env=env,
            )
        except OSError as exc:
            self.wfile.write(f"failed to start: {exc}\n\x1e{json.dumps({'exit': 127})}".encode())
            return
        timer = threading.Timer(COMMAND_TIMEOUT_SECONDS, proc.kill)
        timer.start()
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, b""):
                self.wfile.write(line)
                self.wfile.flush()
            code = proc.wait()
        except (BrokenPipeError, ConnectionResetError):
            proc.kill()
            return
        finally:
            timer.cancel()
        elapsed = round(time.monotonic() - started, 1)
        self.wfile.write(f"\x1e{json.dumps({'exit': code, 'seconds': elapsed})}".encode())


def serve(org_dir: Path, port: int = 8765, env: str | None = None, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(24)
    server = ConsoleServer(port, org_dir.resolve(), env, token)
    print(f"HiveMind console for {org_dir.resolve()}")
    print(f"  {server.url}")
    print("  (local only; press Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
