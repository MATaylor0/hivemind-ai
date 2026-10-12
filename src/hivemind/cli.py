"""``hivemind`` command line.

hivemind validate              check specs, references, hierarchy and policies
hivemind tree                  show the org chart (text or mermaid)
hivemind render <agent>        show the compiled instructions and Foundry definition
hivemind inventory             list the fleet (table / markdown / json), optionally with deployed state
hivemind budget                declared monthly budgets by team and cost centre
hivemind new <agent>           scaffold a new agent from a role
hivemind plan  -e <env>        show what apply would change
hivemind apply -e <env>        deploy new agent versions (and schedules) to Foundry
hivemind publish <agent> -e    publish an agent to Microsoft 365 Copilot and Teams
hivemind invoke <agent> <msg>  send a message to a deployed agent
hivemind hub serve -e <env>    run the HiveMind hub MCP server
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .compiler import compile_agent, compile_org, foundry_name, shared_memory_store
from .instructions import InstructionError, SourceResolver
from .loader import LoadError, Org, load_org
from .planner import apply_plan, make_plan
from .policy import check_org, has_errors
from .reporting import budget_summary, inventory, org_chart, render_inventory, tiers
from .workspace import workspace_for


def _git_sha(root: Path) -> str | None:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha[:12]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _load(args: argparse.Namespace, env: str | None = None) -> Org:
    return load_org(args.org, env=env if env is not None else getattr(args, "env", None))


def _validated(args: argparse.Namespace, env: str | None = None) -> Org:
    org = _load(args, env)
    findings = check_org(org)
    for finding in findings:
        print(finding, file=sys.stderr)
    if has_errors(findings):
        raise SystemExit(1)
    return org


def cmd_validate(args: argparse.Namespace) -> int:
    org = _load(args)
    findings = check_org(org)
    if not has_errors(findings):
        # Compile everything too, so missing instruction files and specs are caught in CI.
        try:
            compile_org(org, SourceResolver(org.root, offline=True, workspace=workspace_for(org)))
        except Exception as exc:  # noqa: BLE001 - report any compile failure
            print(f"ERROR   compile: {exc}")
            return 1
    for finding in findings:
        print(finding)
    errors = sum(f.level == "error" for f in findings)
    warnings = len(findings) - errors
    print(
        f"\n{len(org.agents)} agents, {len(org.roles)} roles, {len(org.tools)} tools, "
        f"{len(org.knowledge)} knowledge sources: {errors} errors, {warnings} warnings"
    )
    return 1 if errors or (args.strict and warnings) else 0


def cmd_tree(args: argparse.Namespace) -> int:
    org = _load(args)
    chart = org_chart(org)
    print(chart.render_mermaid(tiers(org)) if args.format == "mermaid" else chart.render_text(tiers(org)))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    org = _validated(args)
    if args.agent not in org.agents:
        print(f"unknown agent '{args.agent}'", file=sys.stderr)
        return 1
    build = compile_agent(org, args.agent, SourceResolver(org.root, offline=args.offline, workspace=workspace_for(org)))
    if args.json:
        print(
            json.dumps(
                {
                    "name": build.name,
                    "description": build.description,
                    "definition": build.definition,
                    "metadata": build.metadata,
                },
                indent=2,
            )
        )
    else:
        print(build.instructions)
        print("-" * 80)
        print(f"name:     {build.name}\nmodel:    {build.definition['model']}\nhash:     {build.content_hash}")
        print(f"tools:    {', '.join(t.get('server_label') or t['type'] for t in build.definition['tools'])}")
        print(f"sources:  {', '.join(build.instruction_sources)}")
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    org = _load(args)
    deployed = None
    if args.env:
        from .providers import provider_for

        deployed = provider_for(org).list_managed()
    print(render_inventory(inventory(org, deployed), args.format))
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    print(budget_summary(_load(args)))
    return 0


def cmd_plan(args: argparse.Namespace, apply: bool = False) -> int:
    from .providers import provider_for

    org = _validated(args)
    builds = compile_org(org, SourceResolver(org.root, workspace=workspace_for(org)), git_sha=_git_sha(org.root))
    provider = provider_for(org)
    plan = make_plan(builds, provider.list_managed())
    print(f"Environment: {org.env_name} ({org.env.provider})\n")
    print(plan.render())
    if not apply:
        return 2 if (args.detailed_exitcode and plan.has_changes) else 0
    if not plan.has_changes:
        print("\nNothing to do.")
        return 0
    if not args.yes:
        answer = input("\nApply these changes? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1
    print()
    shared = shared_memory_store(org)
    if shared and provider.ensure_memory_store(shared):
        print(f"  created shared memory store {shared.name}")
    apply_plan(plan, provider, prune=args.prune)
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from .providers import provider_for

    org = _validated(args)
    agent = org.agents.get(args.agent)
    if agent is None:
        print(f"unknown agent '{args.agent}'", file=sys.stderr)
        return 1
    if not agent.channels.m365.enabled:
        print(f"{args.agent}: channels.m365.enabled is false; enable it in the spec first", file=sys.stderr)
        return 1
    result = provider_for(org).publish_m365(foundry_name(org, agent.name), agent.channels.m365, agent.description)
    print(result)
    return 0


def cmd_invoke(args: argparse.Namespace) -> int:
    from .providers import provider_for

    org = _load(args)
    print(provider_for(org).invoke(foundry_name(org, args.agent), args.message))
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    org = _load(args)
    if args.agent in org.agents:
        print(f"agent '{args.agent}' already exists", file=sys.stderr)
        return 1
    if args.role not in org.roles:
        print(f"unknown role '{args.role}'. Roles: {', '.join(sorted(org.roles))}", file=sys.stderr)
        return 1
    folder = org.root / "agents" / (args.team or "")
    folder.mkdir(parents=True, exist_ok=True)
    instructions = org.root / "instructions" / "agents" / f"{args.agent}.md"
    instructions.parent.mkdir(parents=True, exist_ok=True)
    spec_path = folder / f"{args.agent}.yaml"
    lines = [
        "apiVersion: hivemind/v1",
        "kind: Agent",
        f"name: {args.agent}",
        f"description: {args.description or 'TODO: one sentence on what this agent is for.'}",
        f"role: {args.role}",
    ]
    if args.team:
        lines.append(f"team: {args.team}")
    lines.append(f"owner: {args.owner or 'TODO@your-org.com'}")
    if args.reports_to:
        lines.append(f"reports_to: {args.reports_to}")
    lines += [
        "instructions:",
        f"  - file: instructions/agents/{args.agent}.md",
        "tools: []",
        "knowledge: []",
        "budget:",
        "  monthly_usd: 50",
    ]
    spec_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not instructions.exists():
        instructions.write_text(
            f"Describe what {args.agent} does, the outcomes it owns, how it should work and when it must stop "
            "and ask for help.\n",
            encoding="utf-8",
        )
    print(f"created {spec_path.relative_to(org.root)}\ncreated {instructions.relative_to(org.root)}")
    print("next: edit both files, then run `hivemind validate` and `hivemind render " + args.agent + "`")
    return 0


def cmd_pause(args: argparse.Namespace, enabled: bool) -> int:
    from .providers import provider_for

    org = _load(args)
    if args.agent not in org.agents:
        print(f"unknown agent '{args.agent}'", file=sys.stderr)
        return 1
    provider_for(org).set_enabled(foundry_name(org, args.agent), enabled)
    print(f"{'resumed' if enabled else 'paused'} {foundry_name(org, args.agent)} in {org.env_name}")
    return 0


def cmd_identities(args: argparse.Namespace) -> int:
    """Agent identities, for granting published agents access to downstream resources (RBAC)."""
    from .providers import provider_for

    org = _load(args)
    deployed = provider_for(org).list_managed()
    shared: dict[str, int] = {}
    for agent in deployed.values():
        if agent.principal_id:
            shared[agent.principal_id] = shared.get(agent.principal_id, 0) + 1
    for name, agent in sorted(deployed.items()):
        kind = "shared project identity" if shared.get(agent.principal_id or "", 0) > 1 else "own identity"
        print(f"{name:32} {agent.principal_id or '-':38} {kind if agent.principal_id else 'unknown'}")
    return 0


def cmd_sharepoint_provision(args: argparse.Namespace) -> int:
    from .sharepoint import provision

    provision(_validated(args))
    return 0


def _task_store(org: Org):
    from .tasks import task_store_for

    return task_store_for(org)


def cmd_tasks_list(args: argparse.Namespace) -> int:
    org = _load(args)
    tasks = _task_store(org).query(status=args.status, assigned_to=args.agent, limit=args.limit)
    if args.json:
        print(json.dumps([t.to_dict() for t in tasks], indent=2))
        return 0
    for t in tasks:
        parent = f" <- {t.parent_id}" if t.parent_id else ""
        print(f"{t.id:8} {t.status:10} {t.assigned_to:22} {t.kind:10} {t.title[:60]}{parent}")
    print(f"\n{len(tasks)} task(s)")
    return 0


def cmd_tasks_show(args: argparse.Namespace) -> int:
    task = _task_store(_load(args)).get(args.task_id)
    if task is None:
        print(f"task '{args.task_id}' not found", file=sys.stderr)
        return 1
    print(json.dumps(task.to_dict(), indent=2))
    return 0


def cmd_tasks_add(args: argparse.Namespace) -> int:
    from .tasks import Task

    org = _load(args)
    if args.agent not in org.active_agents():
        print(f"unknown or inactive agent '{args.agent}'", file=sys.stderr)
        return 1
    if args.goal and args.goal not in org.config.goals:
        print(f"unknown goal '{args.goal}'", file=sys.stderr)
        return 1
    task = _task_store(org).create(
        Task(
            title=args.title or args.instructions.splitlines()[0][:120],
            instructions=args.instructions,
            assigned_to=args.agent,
            requested_by=args.requested_by or os.environ.get("USER", "cli"),
            requester_kind="person",
            goal=args.goal,
            depth=0,
        )
    )
    print(f"created {task.id} for {task.assigned_to}")
    return 0


def cmd_tasks_run(args: argparse.Namespace) -> int:
    """Work the queue once, in this process (local environments and troubleshooting)."""
    from .hub.server import build_service
    from .hub.worker import TaskRunner

    runner = TaskRunner(build_service(_validated(args)))
    count = runner.run_pending()
    delivered = runner.deliver_follow_ups()
    print(f"ran {count} task(s), delivered {delivered} follow-up(s)")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui.server import serve

    org = _load(args, env=None)
    env = args.env or next((n for n, e in org.config.environments.items() if e.provider == "local"), None)
    serve(org.root, port=args.port, env=env, open_browser=not args.no_browser)
    return 0


def cmd_hub_serve(args: argparse.Namespace) -> int:
    from .hub.server import serve

    org = _validated(args)
    serve(org, host=args.host, port=args.port, insecure=args.insecure)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hivemind", description="Agents-as-code for Microsoft Foundry.")
    parser.add_argument("--org", default=os.environ.get("HIVEMIND_ORG", "."), help="org directory (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_env(p: argparse.ArgumentParser, required: bool) -> None:
        p.add_argument(
            "-e",
            "--env",
            required=required,
            default=os.environ.get("HIVEMIND_ENV") if not required else None,
            help="environment from hivemind.yaml",
        )

    p = sub.add_parser("validate", help="validate the org directory")
    with_env(p, False)
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("tree", help="show the org chart")
    p.add_argument("--format", choices=["text", "mermaid"], default="text")
    p.set_defaults(func=cmd_tree, env=None)

    p = sub.add_parser("render", help="show an agent's compiled instructions and definition")
    p.add_argument("agent")
    with_env(p, False)
    p.add_argument("--json", action="store_true")
    p.add_argument("--offline", action="store_true", help="do not fetch SharePoint/URL instruction sources")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("inventory", help="list the agent fleet")
    with_env(p, False)
    p.add_argument("--format", choices=["table", "markdown", "json"], default="table")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("budget", help="declared monthly budgets")
    p.set_defaults(func=cmd_budget, env=None)

    p = sub.add_parser("new", help="scaffold a new agent")
    p.add_argument("agent")
    p.add_argument("--role", required=True)
    p.add_argument("--team")
    p.add_argument("--reports-to")
    p.add_argument("--owner")
    p.add_argument("--description")
    p.set_defaults(func=cmd_new, env=None)

    for name, apply in (("plan", False), ("apply", True)):
        p = sub.add_parser(name, help=f"{name} deployment to an environment")
        with_env(p, True)
        if apply:
            p.add_argument("--prune", action="store_true", help="delete managed agents no longer defined")
            p.add_argument("--yes", "-y", action="store_true", help="do not prompt for confirmation")
            p.set_defaults(func=lambda a: cmd_plan(a, apply=True), detailed_exitcode=False)
        else:
            p.add_argument("--detailed-exitcode", action="store_true", help="exit 2 when there are changes")
            p.set_defaults(func=cmd_plan)

    p = sub.add_parser("publish", help="publish an agent to Microsoft 365 Copilot and Teams")
    p.add_argument("agent")
    with_env(p, True)
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("invoke", help="send a message to a deployed agent")
    p.add_argument("agent")
    p.add_argument("message")
    with_env(p, True)
    p.set_defaults(func=cmd_invoke)

    for name, enabled in (("pause", False), ("resume", True)):
        p = sub.add_parser(name, help=f"{name} an agent in the environment (Foundry enable/disable)")
        p.add_argument("agent")
        with_env(p, True)
        p.set_defaults(func=lambda a, e=enabled: cmd_pause(a, e))

    p = sub.add_parser("identities", help="list deployed agents' Entra identities (for RBAC)")
    with_env(p, True)
    p.set_defaults(func=cmd_identities)

    sp = sub.add_parser("sharepoint", help="HiveMind SharePoint site")
    sp_sub = sp.add_subparsers(dest="sharepoint_command", required=True)
    p = sp_sub.add_parser("provision", help="create/update the rules and outputs libraries and the tasks list")
    with_env(p, True)
    p.set_defaults(func=cmd_sharepoint_provision)

    tasks = sub.add_parser("tasks", help="the task queue")
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    p = tasks_sub.add_parser("list", help="list tasks")
    with_env(p, True)
    p.add_argument("--status", choices=["Queued", "InProgress", "Done", "Failed", "Cancelled"])
    p.add_argument("--agent")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tasks_list)
    p = tasks_sub.add_parser("show", help="show one task")
    p.add_argument("task_id")
    with_env(p, True)
    p.set_defaults(func=cmd_tasks_show)
    p = tasks_sub.add_parser("add", help="give an agent a task (as a person)")
    p.add_argument("agent")
    p.add_argument("instructions")
    p.add_argument("--title")
    p.add_argument("--goal")
    p.add_argument("--requested-by")
    with_env(p, True)
    p.set_defaults(func=cmd_tasks_add)
    p = tasks_sub.add_parser("run", help="work the queue once in this process")
    with_env(p, True)
    p.set_defaults(func=cmd_tasks_run)

    p = sub.add_parser("ui", help="open the local admin console (org views + a console for these commands)")
    p.add_argument("-e", "--env", help="environment selected when the console opens (default: the local one)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_ui)

    hub = sub.add_parser("hub", help="HiveMind hub")
    hub_sub = hub.add_subparsers(dest="hub_command", required=True)
    p = hub_sub.add_parser("serve", help="run the hub MCP server")
    with_env(p, True)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    p.add_argument("--insecure", action="store_true", help="key auth without HIVEMIND_HUB_KEY (dev only)")
    p.set_defaults(func=cmd_hub_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except LoadError as exc:
        print(f"error loading org:\n{exc}", file=sys.stderr)
        return 1
    except InstructionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
