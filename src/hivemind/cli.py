"""``hivemind`` command line.

hivemind validate              check specs, references, hierarchy and policies
hivemind tree                  show the org chart (text or mermaid)
hivemind render <agent>        show the compiled instructions and Foundry definition
hivemind inventory             list the fleet (table / markdown / json), optionally with deployed state
hivemind budget                declared monthly budgets by team and cost centre
hivemind new <agent>           scaffold a new agent from a role
hivemind build -e <env>        generate the azd project (azure.yaml) for an environment
hivemind plan  -e <env>        show what apply would change
hivemind apply -e <env>        deploy: `azd deploy` of the generated project (or the SDK/local provider)
hivemind gate  -e <env>        run each agent's eval gate (pass rate for its autonomy level)
hivemind evals add <agent>     add a golden case (e.g. a human correction) to an agent's eval dataset
hivemind process run <name>    run a process locally (Agent Framework workflow, local agents)
hivemind publish <agent> -e    publish an agent to Microsoft 365 Copilot and Teams
hivemind invoke <agent> <msg>  send a message to a deployed agent
hivemind pause|resume <agent>  disable / enable an agent in Foundry
hivemind identities -e <env>   deployed agents' Entra identities
hivemind sharepoint provision  create the business-owned rules library on the HiveMind site
hivemind ui                    local admin console in the browser
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .compiler import compile_agent, compile_org, foundry_name
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
        f"\n{len(org.agents)} agents, {len(org.processes)} processes, {len(org.roles)} roles, {len(org.tools)} tools, "
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
        print(f"autonomy: {build.autonomy.value}")
        print(f"tools:    {', '.join(t.get('server_label') or t['type'] for t in build.definition['tools'])}")
        if build.gated_tools:
            print(f"gated:    {', '.join(build.gated_tools)} (require approval at this autonomy level)")
        if build.removed_tools:
            print(f"held:     {', '.join(build.removed_tools)} (not available at this autonomy level)")
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


def _build_dir(org: Org, out: str | None = None) -> Path:
    return Path(out).resolve() if out else org.root / ".hivemind" / "build" / (org.env_name or "local")


def cmd_build(args: argparse.Namespace) -> int:
    from .azd import build

    org = _validated(args)
    resolver = SourceResolver(org.root, offline=args.offline, workspace=workspace_for(org))
    result = build(org, _build_dir(org, args.out), resolver, _git_sha(org.root))
    if args.offline:
        print("note: --offline: SharePoint and URL instruction sources were not fetched", file=sys.stderr)
    services = result.document["services"]
    print(f"wrote {result.azure_yaml}")
    print(f"  {len(result.agents)} agents, {len(result.processes)} processes, {len(services)} azd services")
    print(f"next: cd {result.root} && azd deploy --all   (or: hivemind apply -e {org.env_name})")
    return 0


def _run(command: list[str], cwd: Path) -> int:
    print(f"$ {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=cwd, check=False).returncode  # noqa: S603 - fixed executables


def _azd_apply(org: Org, args: argparse.Namespace) -> int:
    from .azd import build

    if shutil.which("azd") is None:
        print(
            "azd is not installed. Install the Azure Developer CLI and its Foundry extensions:\n"
            "  azd extension install azure.ai.agents azure.ai.projects azure.ai.routines azure.ai.evaluations\n"
            "or set deploy_engine: sdk for this environment.",
            file=sys.stderr,
        )
        return 1
    result = build(org, _build_dir(org), SourceResolver(org.root, workspace=workspace_for(org)), _git_sha(org.root))
    env_dir = result.root / ".azure" / org.env_name
    if not env_dir.exists():
        command = ["azd", "env", "new", org.env_name, "--no-prompt"]
        for flag, var in (("--subscription", "AZURE_SUBSCRIPTION_ID"), ("--location", "AZURE_LOCATION")):
            if os.environ.get(var):
                command += [flag, os.environ[var]]
        if _run(command, result.root) != 0:
            return 1
    # The registry azd pushes process host images to (infra output registryLoginServer).
    registry = os.environ.get("AZURE_CONTAINER_REGISTRY_ENDPOINT")
    if (
        registry
        and _run(["azd", "env", "set", "AZURE_CONTAINER_REGISTRY_ENDPOINT", registry, "-e", org.env_name], result.root)
        != 0
    ):
        return 1
    return _run(["azd", "deploy", "--all", "--no-prompt", "-e", org.env_name], result.root)


def cmd_plan(args: argparse.Namespace, apply: bool = False) -> int:
    from .providers import provider_for

    org = _validated(args)
    builds = compile_org(org, SourceResolver(org.root, workspace=workspace_for(org)), git_sha=_git_sha(org.root))
    provider = provider_for(org)
    plan = make_plan(builds, provider.list_managed())
    engine = "local" if org.env.provider == "local" else org.env.deploy_engine
    print(f"Environment: {org.env_name} ({org.env.provider}, deploy engine: {engine})\n")
    print(plan.render())
    if org.active_processes():
        print(f"Processes (deployed as hosted agents by azd): {', '.join(sorted(org.active_processes()))}")
    if not apply:
        return 2 if (args.detailed_exitcode and plan.has_changes) else 0
    if engine != "azd" and not plan.has_changes:
        print("\nNothing to do.")
        return 0
    if not args.yes:
        answer = input("\nApply these changes? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1
    print()
    if engine == "azd":
        return _azd_apply(org, args)
    apply_plan(plan, provider, prune=args.prune)
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    from .evals import dataset_rows, gate, local_gate, pass_rate, regression_eval_name

    org = _validated(args)
    agents = [a for n, a in sorted(org.active_agents().items()) if not args.agent or n in args.agent]
    deployed = None
    if org.env.provider == "local":
        from .providers import provider_for

        provider = provider_for(org)
        deployed = provider.list_managed()
    passed_count = failures = skipped = 0
    for agent in agents:
        rows = dataset_rows(org, agent)
        threshold = pass_rate(org, agent)
        if not rows:
            if gate(org, agent).min_cases:
                print(f"FAIL  {agent.name}: no eval dataset")
                failures += 1
            else:
                print(f"SKIP  {agent.name}: no golden dataset ({agent.autonomy.level.value} needs none)")
                skipped += 1
            continue
        if deployed is not None:
            if foundry_name(org, agent.name) not in deployed:
                print(f"FAIL  {agent.name}: not deployed in '{org.env_name}' (run `hivemind apply -e {org.env_name}`)")
                failures += 1
                continue
            passed, total = local_gate(org, agent, provider.invoke)
            rate = passed / total if total else 0.0
            ok = rate >= threshold
            print(
                f"{'PASS' if ok else 'FAIL'}  {agent.name}: {passed}/{total} answered (needs {threshold:.0%}; "
                "local smoke run - quality is graded by Foundry evaluators)"
            )
        else:
            command = [
                "azd",
                "ai",
                "eval",
                "run",
                "start",
                "--eval",
                regression_eval_name(foundry_name(org, agent.name)),
                "--fail-on",
                f"pass-rate={threshold:g}",
                "--no-prompt",
                "-e",
                org.env_name,
            ]
            if args.dry_run:
                print(" ".join(command))
                continue
            ok = _run(command, _build_dir(org)) == 0
            print(f"{'PASS' if ok else 'FAIL'}  {agent.name} (needs {threshold:.0%})")
        passed_count += 1 if ok else 0
        failures += 0 if ok else 1
    if not args.dry_run:
        print(f"\n{passed_count} passed, {failures} failed, {skipped} skipped")
    return 1 if failures else 0


def cmd_evals_add(args: argparse.Namespace) -> int:
    from .evals import add_case

    org = _load(args)
    if args.agent not in org.agents:
        print(f"unknown agent '{args.agent}'", file=sys.stderr)
        return 1
    path = add_case(org, org.agents[args.agent], args.query, args.expected, args.note)
    print(f"added a case to {path.relative_to(org.root)}")
    return 0


def cmd_process_list(args: argparse.Namespace) -> int:
    from .processes import ProcessError, load_process

    org = _load(args)
    for name, spec in sorted(org.processes.items()):
        try:
            info = load_process(org, spec)
            steps = " -> ".join(f"{s.agent or s.kind}" for s in info.steps)
        except ProcessError as exc:
            steps = f"ERROR {exc}"
        print(f"{name:24} owner={spec.owner or '-'}  {steps}")
    return 0


def cmd_process_run(args: argparse.Namespace) -> int:
    import asyncio

    from .processes import LocalProcessRun
    from .providers import provider_for

    org = _validated(args)
    if args.name not in org.processes:
        print(f"unknown process '{args.name}'", file=sys.stderr)
        return 1
    if org.env.provider != "local":
        print(
            "process run works against local environments; deployed processes are hosted agents "
            f"(invoke `{foundry_name(org, 'process-' + args.name)}`)",
            file=sys.stderr,
        )
        return 1
    provider = provider_for(org)

    def invoke(agent: str, text: str) -> str:
        return provider.invoke(foundry_name(org, agent), text)

    def answer(prompt: str) -> str | None:
        if args.answer is not None:
            print(f"? {prompt}\n> {args.answer}")
            return args.answer
        if sys.stdin.isatty():
            return input(f"? {prompt}\n> ")
        return None

    from .processes import ProcessError

    try:
        result = asyncio.run(LocalProcessRun(org, args.name, invoke).run(args.input, answer))
    except ProcessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - an agent step failed; say which, without a traceback
        cause = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        print(f"process stopped: {exc}", file=sys.stderr)
        if isinstance(cause, KeyError):
            print(f"hint: deploy the agents first with `hivemind apply -e {org.env_name}`", file=sys.stderr)
        return 1
    for output in result.outputs:
        print(output)
    if result.status == "awaiting_input":
        for request in result.requests:
            print(f"awaiting input: {request['prompt']}")
    print(f"[{result.status}]")
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
        "# New agents start in shadow mode: they work alongside a person and take no actions. Promote them by",
        "# pull request once the eval dataset shows they are ready (see docs/autonomy-and-evals.md).",
        "autonomy:",
        "  level: shadow",
        "evals:",
        f"  dataset: evals/{args.agent}.jsonl",
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
    dataset = org.root / "evals" / f"{args.agent}.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.touch(exist_ok=True)
    print(f"created {spec_path.relative_to(org.root)}\ncreated {instructions.relative_to(org.root)}")
    print(f"created {dataset.relative_to(org.root)} (add golden cases with `hivemind evals add {args.agent}`)")
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


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui.server import serve

    org = _load(args, env=None)
    env = args.env or next((n for n, e in org.config.environments.items() if e.provider == "local"), None)
    serve(org.root, port=args.port, env=env, open_browser=not args.no_browser)
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

    p = sub.add_parser("build", help="generate the azd project (azure.yaml) for an environment")
    with_env(p, True)
    p.add_argument("--out", help="output directory (default: <org>/.hivemind/build/<env>)")
    p.add_argument("--offline", action="store_true", help="skip SharePoint/URL sources (dry runs and CI checks)")
    p.set_defaults(func=cmd_build)

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

    p = sub.add_parser("gate", help="run eval gates: each agent's pass rate for its autonomy level")
    with_env(p, True)
    p.add_argument("--agent", action="append", help="limit to these agents (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="print the azd commands instead of running them")
    p.set_defaults(func=cmd_gate)

    evals = sub.add_parser("evals", help="eval datasets")
    evals_sub = evals.add_subparsers(dest="evals_command", required=True)
    p = evals_sub.add_parser("add", help="add a golden case to an agent's dataset")
    p.add_argument("agent")
    p.add_argument("--query", required=True)
    p.add_argument("--expected")
    p.add_argument("--note", help="where the case came from, e.g. 'correction by ap.lead, 12 Oct'")
    p.set_defaults(func=cmd_evals_add, env=None)

    proc = sub.add_parser("process", help="processes (Agent Framework workflows run by agents)")
    proc_sub = proc.add_subparsers(dest="process_command", required=True)
    p = proc_sub.add_parser("list", help="list processes and their steps")
    p.set_defaults(func=cmd_process_list, env=None)
    p = proc_sub.add_parser("run", help="run a process locally")
    p.add_argument("name")
    p.add_argument("input")
    p.add_argument("--answer", help="answer every human step with this text (e.g. 'approved')")
    with_env(p, True)
    p.set_defaults(func=cmd_process_run)

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
    p = sp_sub.add_parser("provision", help="create/update the rules library and a folder per org/role/team/agent")
    with_env(p, True)
    p.set_defaults(func=cmd_sharepoint_provision)

    p = sub.add_parser("ui", help="open the local admin console (org views + a console for these commands)")
    p.add_argument("-e", "--env", help="environment selected when the console opens (default: the local one)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    import logging

    logging.getLogger("azure.identity").setLevel(logging.ERROR)  # sign-in failures are reported once, below
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
