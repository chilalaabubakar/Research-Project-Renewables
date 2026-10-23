"""Load the YAML case file and read parameter values.

Each parameter in the YAML is a small dict ``{value, unit, source}``. Code
should always go through :func:`param` so that a missing value or a missing
citation fails loudly instead of silently using a number from somewhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Repo root = two levels above src/topside/
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "base_case.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def param(cfg: dict, dotted_key: str) -> Any:
    """Return ``value`` of a parameter, e.g. ``param(cfg, "wind_turbine.hub_height")``."""
    node: Any = cfg
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"Parameter '{dotted_key}' not found in config")
        node = node[part]
    if not isinstance(node, dict) or "value" not in node:
        raise KeyError(f"'{dotted_key}' is not a parameter (needs value/unit/source)")
    if not node.get("source"):
        raise ValueError(f"Parameter '{dotted_key}' has no source/citation")
    return node["value"]


def repo_path(relative: str | Path) -> Path:
    """Resolve a path from the config relative to the repo root."""
    p = Path(relative)
    return p if p.is_absolute() else REPO_ROOT / p
