"""Loop engineering: every agent's behaviour is measured before it ships and watched after.

* **Golden dataset** (``evals.dataset``): JSONL rows ``{"query": ..., "expected": ...}`` maintained in git.
  Human corrections from production become new rows (``hivemind evals add``).
* **Gate**: before an agent is promoted (dev -> prod) its dataset is run through Foundry evaluations and must
  meet the pass rate for its autonomy level (``policies.evals.gates``). Runs as
  ``azd ai eval run start --fail-on pass-rate=<n>``.
* **Production evaluation**: the same evaluators score a sample of real traces, so drift shows up as a falling
  pass rate rather than a complaint.

Everything compiles to an ``azure.ai.eval`` service per agent in the generated ``azure.yaml``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .loader import Org
from .schema import AgentSpec, EvalGate


class EvalDataError(Exception):
    pass


def dataset_path(org: Org, agent: AgentSpec) -> Path | None:
    if not agent.evals.dataset:
        return None
    path = (org.root / agent.evals.dataset).resolve()
    if org.root not in path.parents:
        raise EvalDataError(f"{agent.name}: eval dataset path escapes the org directory")
    return path


def dataset_rows(org: Org, agent: AgentSpec) -> list[dict[str, Any]]:
    path = dataset_path(org, agent)
    if path is None:
        return []
    if not path.is_file():
        raise EvalDataError(f"{agent.name}: eval dataset not found: {agent.evals.dataset}")
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalDataError(f"{agent.evals.dataset}:{number}: invalid JSON ({exc.msg})") from exc
        if not isinstance(row, dict) or not str(row.get("query", "")).strip():
            raise EvalDataError(f"{agent.evals.dataset}:{number}: each row needs a non-empty 'query'")
        rows.append(row)
    return rows


def gate(org: Org, agent: AgentSpec) -> EvalGate:
    """The evidence required for the agent's *declared* autonomy level."""
    return org.config.policies.evals.gates.get(agent.autonomy.level, EvalGate())


def pass_rate(org: Org, agent: AgentSpec) -> float:
    return agent.evals.pass_rate if agent.evals.pass_rate is not None else gate(org, agent).pass_rate


def evaluators(org: Org, agent: AgentSpec) -> list[str]:
    return agent.evals.evaluators or org.config.policies.evals.evaluators


def regression_eval_name(agent_name: str) -> str:
    return f"{agent_name}-regression"


def production_eval_name(agent_name: str) -> str:
    return f"{agent_name}-production"


def add_case(org: Org, agent: AgentSpec, query: str, expected: str | None = None, note: str | None = None) -> Path:
    """Append a golden row (e.g. a human correction from production) to the agent's dataset."""
    path = dataset_path(org, agent)
    if path is None:
        raise EvalDataError(f"{agent.name}: set evals.dataset in the agent spec first")
    row: dict[str, Any] = {"query": query}
    if expected:
        row["expected"] = expected
    if note:
        row["note"] = note
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def eval_service(
    org: Org, agent: AgentSpec, agent_service: str, foundry_agent_name: str, dataset_file: str | None
) -> dict[str, Any] | None:
    """The ``azure.ai.eval`` service body for one agent, or None when it has nothing to evaluate."""
    policy = org.config.policies.evals
    init = {"model": policy.judge_model} if policy.judge_model else None

    def refs() -> list[dict[str, Any]]:
        out = []
        for name in evaluators(org, agent):
            ref: dict[str, Any] = {"evaluator": name}
            if init and name.startswith("builtin."):
                ref["initializationParameters"] = dict(init)
            out.append(ref)
        return out

    service: dict[str, Any] = {"datasets": [], "evals": []}
    if dataset_file:
        dataset = f"{foundry_agent_name}-golden"
        service["datasets"].append({"name": dataset, "file": dataset_file})
        service["evals"].append(
            {
                "name": regression_eval_name(foundry_agent_name),
                "description": f"Golden dataset for {agent.title}; gates promotion.",
                "dataset": dataset,
                "evaluationLevel": "turn",
                "evaluators": refs(),
                "target": {"type": "agent", "name": agent_service},
            }
        )
    if agent.evals.trace_eval:
        service["evals"].append(
            {
                "name": production_eval_name(foundry_agent_name),
                "description": f"A sample of {agent.title}'s real production traces.",
                "source": {
                    "type": "traces",
                    "agentName": foundry_agent_name,
                    "lookbackHours": policy.trace_lookback_hours,
                    "maxTraces": policy.trace_sample,
                },
                "evaluators": refs(),
            }
        )
    if not service["evals"]:
        return None
    if not service["datasets"]:
        del service["datasets"]
    return service


def local_gate(org: Org, agent: AgentSpec, invoke: Callable[[str, str], str]) -> tuple[int, int]:
    """Local smoke run of the gate: every golden case must get an answer without errors.

    Quality is graded by Foundry's evaluators in real environments (``azd ai eval``); locally there is no model to
    judge against ``expected``, so this checks the plumbing (dataset, agent, invocation) end to end.
    """
    from .compiler import foundry_name

    passed = 0
    rows = dataset_rows(org, agent)
    for row in rows:
        try:
            answer = invoke(foundry_name(org, agent.name), str(row["query"]))
        except Exception:  # noqa: BLE001 - a failing case is a failed case
            continue
        if answer.strip():
            passed += 1
    return passed, len(rows)
