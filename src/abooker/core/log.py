"""Logging configuration loader with local override support."""
import logging.config
import os
from pathlib import Path

import yaml

_DEFAULT_CONFIG = Path(__file__).parent.parent / "logging.yaml"
_LOCAL_NAMES = ["logging.local.yaml"]


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def setup_logging() -> None:
    """Load logging config from the bundled default, then deep-merge a local override.

    Local override is searched in (first match wins):
      1. Path in LOG_CONFIG env var
      2. logging.local.yaml in the current working directory
    """
    config: dict = yaml.safe_load(_DEFAULT_CONFIG.read_text())

    local_path: Path | None = None
    if env_path := os.environ.get("LOG_CONFIG"):
        candidate = Path(env_path)
        if candidate.exists():
            local_path = candidate
        else:
            print(f"WARNING: LOG_CONFIG={env_path!r} not found, using defaults")
    else:
        for name in _LOCAL_NAMES:
            candidate = Path.cwd() / name
            if candidate.exists():
                local_path = candidate
                break

    if local_path is not None:
        local: dict = yaml.safe_load(local_path.read_text()) or {}
        config = _deep_merge(config, local)

    logging.config.dictConfig(config)
