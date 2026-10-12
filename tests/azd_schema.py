"""Validate generated azure.yaml against the azd schemas, offline.

The schemas under fixtures/azd-schemas are copied from https://github.com/Azure/azure-dev (MIT licence, see
LICENSE-azure-dev): the root azure.yaml schema and the Foundry extension schemas HiveMind generates for.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft201909Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7, DRAFT201909

FIXTURES = Path(__file__).parent / "fixtures" / "azd-schemas"
BASE = "https://raw.githubusercontent.com/Azure/azure-dev/main/cli/azd/extensions"
IDS = {
    "azure.yaml.json": "https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json",
    "azure.ai.agent.json": f"{BASE}/azure.ai.agents/schemas/azure.ai.agent.json",
    "azure.ai.project.json": f"{BASE}/azure.ai.projects/schemas/azure.ai.project.json",
    "azure.ai.routine.json": f"{BASE}/azure.ai.routines/schemas/azure.ai.routine.json",
    "azure.ai.eval.json": f"{BASE}/azure.ai.evaluations/schemas/azure.ai.eval.json",
}


def validator() -> Draft201909Validator:
    registry = Registry()
    for name, uri in IDS.items():
        contents = json.loads((FIXTURES / name).read_text())
        spec = DRAFT201909 if name == "azure.yaml.json" else DRAFT7
        registry = registry.with_resource(uri, Resource.from_contents(contents, default_specification=spec))
    root = json.loads((FIXTURES / "azure.yaml.json").read_text())
    return Draft201909Validator(root, registry=registry)


def errors(document: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator().iter_errors(document)]
