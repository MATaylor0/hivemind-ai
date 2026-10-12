"""Terraform-style plan/apply: compare compiled builds with what is deployed and reconcile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .compiler import AgentBuild
from .providers.base import DeployedAgent, Provider

Action = Literal["create", "update", "unchanged", "delete"]


@dataclass
class PlanItem:
    action: Action
    name: str
    build: AgentBuild | None = None
    deployed: DeployedAgent | None = None

    @property
    def reason(self) -> str:
        if self.action == "create":
            return "not deployed yet"
        if self.action == "update":
            assert self.deployed is not None and self.build is not None
            return f"content changed ({self.deployed.content_hash} -> {self.build.content_hash})"
        if self.action == "delete":
            return "managed by HiveMind but no longer defined (or disabled)"
        return "up to date"


@dataclass
class Plan:
    items: list[PlanItem] = field(default_factory=list)

    def by_action(self, action: Action) -> list[PlanItem]:
        return [i for i in self.items if i.action == action]

    @property
    def has_changes(self) -> bool:
        return any(i.action != "unchanged" for i in self.items)

    def render(self) -> str:
        symbols = {"create": "+", "update": "~", "delete": "-", "unchanged": " "}
        lines = []
        for item in sorted(self.items, key=lambda i: (i.action == "unchanged", i.name)):
            version = f" (v{item.deployed.version})" if item.deployed else ""
            lines.append(f"  {symbols[item.action]} {item.name}{version}: {item.action} - {item.reason}")
        counts = {a: len(self.by_action(a)) for a in ("create", "update", "delete", "unchanged")}
        lines.append(
            f"\nPlan: {counts['create']} to create, {counts['update']} to update, "
            f"{counts['delete']} to delete, {counts['unchanged']} unchanged."
        )
        return "\n".join(lines)


def make_plan(builds: list[AgentBuild], deployed: dict[str, DeployedAgent]) -> Plan:
    plan = Plan()
    wanted = {b.name: b for b in builds}
    for name, build in wanted.items():
        current = deployed.get(name)
        if current is None:
            plan.items.append(PlanItem("create", name, build=build))
        elif current.content_hash != build.content_hash:
            plan.items.append(PlanItem("update", name, build=build, deployed=current))
        else:
            plan.items.append(PlanItem("unchanged", name, build=build, deployed=current))
    for name, current in deployed.items():
        if name not in wanted:
            plan.items.append(PlanItem("delete", name, deployed=current))
    return plan


def apply_plan(plan: Plan, provider: Provider, prune: bool = False, log: Callable[[str], None] = print) -> list[str]:
    """Execute the plan. Deletes only happen with ``prune`` so a typo can never wipe an agent."""
    done: list[str] = []
    for item in plan.items:
        if item.action in ("create", "update"):
            assert item.build is not None
            build = item.build
            if build.memory_store and provider.ensure_memory_store(build.memory_store):
                log(f"  created memory store {build.memory_store.name}")
            deployed = provider.deploy(build)
            log(f"  {item.action}d {build.name} -> version {deployed.version}")
            for change in provider.sync_schedules(build, build.schedules):
                log(f"    {change}")
            done.append(build.name)
        elif item.action == "delete":
            if prune:
                provider.delete(item.name)
                log(f"  deleted {item.name}")
                done.append(item.name)
            else:
                log(f"  skipped delete of {item.name} (re-run with --prune to remove it)")
    return done
