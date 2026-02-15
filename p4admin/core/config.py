"""
Configuration loading and validation.

Supports YAML configuration files for server connection details,
tool-specific settings, and output preferences.

Example config (server.yaml):
    server:
        port: "ssl:perforce:1666"
        user: "admin"
        charset: "utf8"

    output:
        format: "terminal"    # terminal, json, markdown, html
        color: true
        verbose: false

    licensing:
        support_email: "it@yourstudio.com"
        perforce_rep: "rep@perforce.com"
        renewal_warning_days: 30
        portal_url: "https://www.perforce.com/support"

    thresholds:
        stale_workspace_days: 90
        stale_shelf_days: 60
        max_file_size_mb: 500
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "server": {},
    "output": {
        "format": "terminal",
        "color": True,
        "verbose": False,
    },
    "licensing": {
        "support_email": "",
        "perforce_rep": "",
        "renewal_warning_days": 30,
        "portal_url": "https://www.perforce.com/support",
        "license_renewal_url": "https://www.perforce.com/support/request-license",
    },
    "thresholds": {
        "stale_workspace_days": 90,
        "stale_shelf_days": 60,
        "max_file_size_mb": 500,
        "inactive_user_days": 90,
    },
}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """
    Load configuration from a YAML file, merged with defaults.

    Args:
        config_path: Path to YAML config file. If None, returns defaults.

    Returns:
        Configuration dictionary with defaults applied for missing keys.
    """
    config = _deep_copy_dict(DEFAULT_CONFIG)

    if config_path is None:
        logger.debug("No config file specified, using defaults")
        return config

    config_path = Path(config_path)

    if not config_path.exists():
        logger.warning("Config file not found: %s — using defaults", config_path)
        return config

    try:
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}

        config = _deep_merge(config, user_config)
        logger.info("Loaded config from %s", config_path)
        return config

    except yaml.YAMLError as e:
        logger.error("Failed to parse config file %s: %s", config_path, e)
        raise ValueError(f"Invalid YAML in config file: {e}") from e


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep merge two dictionaries. Override values take precedence.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _deep_copy_dict(d: dict) -> dict:
    """Simple deep copy for nested dicts (avoids copy module import)."""
    result = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = _deep_copy_dict(value)
        elif isinstance(value, list):
            result[key] = value[:]
        else:
            result[key] = value
    return result
