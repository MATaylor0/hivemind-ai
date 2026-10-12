from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from hivemind.loader import Org, load_org

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "contoso"


@pytest.fixture
def org_dir(tmp_path: Path) -> Path:
    """A writable copy of the Contoso example org."""
    target = tmp_path / "contoso"
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns(".hivemind"))
    return target


@pytest.fixture
def org(org_dir: Path) -> Org:
    return load_org(org_dir, env="local")


def edit_yaml(path: Path, **changes) -> None:
    data = yaml.safe_load(path.read_text())
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))
