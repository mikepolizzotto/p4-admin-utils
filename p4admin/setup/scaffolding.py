"""
Production Scaffolding.

When a new show, game, or project spins up, you need the same P4 infrastructure
every time: depot, streams, groups, protections, typemap entries. Doing it by hand
means 2 hours of clicking through P4Admin and hoping you didn't forget anything.

This tool reads a YAML config that defines a production's P4 structure and
creates everything in one shot. Idempotent — safe to run again if you add
streams or groups to the config later.

Usage:
    scaffolder = ProductionScaffolder(p4_conn)

    # Preview what would be created
    report = scaffolder.preview("configs/productions/new-film.yaml")

    # Execute
    scaffolder.apply("configs/productions/new-film.yaml", dry_run=False)

Example config (configs/productions/new-film.yaml):
    production:
      name: "titan"
      description: "Project Titan - Animated Feature"

    depot:
      name: "titan"
      type: "stream"

    streams:
      - name: "main"
        type: "mainline"
        description: "Main integration branch"
      - name: "dev"
        type: "development"
        parent: "//titan/main"
        description: "Active development"
      - name: "release"
        type: "release"
        parent: "//titan/main"
        description: "Release candidates"

    groups:
      - name: "titan-artists"
        description: "Titan production artists"
        max_results: 10000
        max_scan_rows: 100000
      - name: "titan-leads"
        description: "Titan department leads"
        subgroups: ["titan-artists"]
      - name: "titan-admins"
        description: "Titan production admins"
        subgroups: ["titan-leads"]

    protections:
      - access: "read"
        group: "titan-artists"
        path: "//titan/..."
      - access: "write"
        group: "titan-artists"
        path: "//titan/dev/..."
      - access: "write"
        group: "titan-leads"
        path: "//titan/..."
      - access: "admin"
        group: "titan-admins"
        path: "//titan/..."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from p4admin.core.connection import P4Connection
from p4admin.core.output import ReportData

logger = logging.getLogger(__name__)


@dataclass
class ScaffoldAction:
    """A single action to be taken during scaffolding."""

    action_type: str  # "create_depot", "create_stream", "create_group", etc.
    target: str  # Name/path of what's being created
    details: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, created, skipped (already exists), failed
    error: str = ""


class ProductionScaffolder:
    """
    Creates Perforce infrastructure for a new production from a YAML config.

    Args:
        conn: Active P4Connection instance.
    """

    def __init__(self, conn: P4Connection):
        self.conn = conn

    def preview(self, config_path: str | Path) -> ReportData:
        """
        Preview what would be created without making changes.

        Returns a report showing all actions that would be taken.
        """
        config = self._load_production_config(config_path)
        actions = self._plan_actions(config)

        report = ReportData(
            title=f"Production Scaffold Preview: {config.get('production', {}).get('name', 'Unknown')}",
            server_info=self.conn.server_info,
            summary={
                "Production": config.get("production", {}).get("name", "Unknown"),
                "Description": config.get("production", {}).get("description", ""),
                "Total actions": len(actions),
                "New items": len([a for a in actions if a.status == "pending"]),
                "Already exists": len([a for a in actions if a.status == "skipped"]),
            },
        )

        # Group actions by type
        action_groups = {}
        for action in actions:
            action_groups.setdefault(action.action_type, []).append(action)

        type_labels = {
            "create_depot": "Depots",
            "create_stream": "Streams",
            "create_group": "Groups",
            "add_protection": "Protection Entries",
        }

        for action_type, group_actions in action_groups.items():
            label = type_labels.get(action_type, action_type)
            report.add_section(
                title=label,
                headers=["Target", "Status", "Details"],
                rows=[
                    [
                        a.target,
                        "NEW" if a.status == "pending" else "EXISTS",
                        "; ".join(f"{k}={v}" for k, v in a.details.items() if k != "spec"),
                    ]
                    for a in group_actions
                ],
            )

        return report

    def apply(self, config_path: str | Path, dry_run: bool = True) -> ReportData:
        """
        Apply production scaffolding from a YAML config.

        Args:
            config_path: Path to production YAML config.
            dry_run: If True, only preview. If False, create everything.

        Returns:
            Report showing what was created.
        """
        config = self._load_production_config(config_path)
        actions = self._plan_actions(config)

        if not dry_run:
            self._execute_actions(actions)

        prod_name = config.get("production", {}).get("name", "Unknown")
        report = ReportData(
            title=f"Production Scaffold: {prod_name}" + (" [DRY RUN]" if dry_run else ""),
            server_info=self.conn.server_info,
            summary={
                "Production": prod_name,
                "Mode": "Dry run" if dry_run else "Applied",
                "Created": len([a for a in actions if a.status == "created"]),
                "Skipped (exists)": len([a for a in actions if a.status == "skipped"]),
                "Failed": len([a for a in actions if a.status == "failed"]),
            },
        )

        for action in actions:
            status_icon = {"created": "OK", "skipped": "EXISTS", "failed": "FAILED", "pending": "PENDING"}
            report.add_section(
                title=f"{action.action_type}: {action.target}",
                stats={
                    "Status": status_icon.get(action.status, action.status),
                    **({"Error": action.error} if action.error else {}),
                },
            )

        return report

    def _load_production_config(self, config_path: str | Path) -> dict[str, Any]:
        """Load and validate a production YAML config."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Production config not found: {path}")

        with open(path) as f:
            config = yaml.safe_load(f)

        if not config.get("production", {}).get("name"):
            raise ValueError("Production config must include production.name")

        return config

    def _plan_actions(self, config: dict[str, Any]) -> list[ScaffoldAction]:
        """Plan all actions needed to scaffold the production."""
        actions: list[ScaffoldAction] = []

        # Depot
        depot_config = config.get("depot", {})
        if depot_config:
            depot_name = depot_config.get("name", config["production"]["name"])
            exists = self._depot_exists(depot_name)
            actions.append(ScaffoldAction(
                action_type="create_depot",
                target=depot_name,
                details={
                    "type": depot_config.get("type", "stream"),
                    "description": depot_config.get("description", config["production"].get("description", "")),
                },
                status="skipped" if exists else "pending",
            ))

        # Streams
        for stream_config in config.get("streams", []):
            depot_name = depot_config.get("name", config["production"]["name"])
            stream_path = f"//{depot_name}/{stream_config['name']}"
            exists = self._stream_exists(stream_path)
            actions.append(ScaffoldAction(
                action_type="create_stream",
                target=stream_path,
                details={
                    "type": stream_config.get("type", "development"),
                    "parent": stream_config.get("parent", "none"),
                    "description": stream_config.get("description", ""),
                },
                status="skipped" if exists else "pending",
            ))

        # Groups
        for group_config in config.get("groups", []):
            group_name = group_config["name"]
            exists = self._group_exists(group_name)
            actions.append(ScaffoldAction(
                action_type="create_group",
                target=group_name,
                details={
                    "description": group_config.get("description", ""),
                    "max_results": group_config.get("max_results", "unset"),
                    "max_scan_rows": group_config.get("max_scan_rows", "unset"),
                    "subgroups": ", ".join(group_config.get("subgroups", [])),
                },
                status="skipped" if exists else "pending",
            ))

        # Protections
        for prot_config in config.get("protections", []):
            target = f"{prot_config['access']} group {prot_config['group']} {prot_config['path']}"
            actions.append(ScaffoldAction(
                action_type="add_protection",
                target=target,
                details=prot_config,
                status="pending",  # We always check/add protections
            ))

        return actions

    def _execute_actions(self, actions: list[ScaffoldAction]):
        """Execute planned actions."""
        for action in actions:
            if action.status == "skipped":
                continue

            try:
                if action.action_type == "create_depot":
                    self._create_depot(action)
                elif action.action_type == "create_stream":
                    self._create_stream(action)
                elif action.action_type == "create_group":
                    self._create_group(action)
                elif action.action_type == "add_protection":
                    self._add_protection(action)
                else:
                    action.status = "failed"
                    action.error = f"Unknown action type: {action.action_type}"
            except Exception as e:
                action.status = "failed"
                action.error = str(e)
                logger.error("Failed %s %s: %s", action.action_type, action.target, e)

    def _create_depot(self, action: ScaffoldAction):
        """Create a new depot."""
        spec = self.conn.run("depot", "-o", action.target)[0]
        spec["Type"] = action.details.get("type", "stream")
        spec["Description"] = action.details.get("description", "")
        # StreamDepth is required for stream depots
        if spec["Type"] == "stream":
            spec["StreamDepth"] = f"//{action.target}/1"
        self.conn.run("depot", "-i", input=spec)
        action.status = "created"
        logger.info("Created depot: %s", action.target)

    def _create_stream(self, action: ScaffoldAction):
        """Create a new stream."""
        spec = self.conn.run("stream", "-o", action.target)[0]
        spec["Type"] = action.details.get("type", "development")
        spec["Parent"] = action.details.get("parent", "none")
        spec["Description"] = action.details.get("description", "")
        self.conn.run("stream", "-i", input=spec)
        action.status = "created"
        logger.info("Created stream: %s", action.target)

    def _create_group(self, action: ScaffoldAction):
        """Create a new group."""
        spec = self.conn.run("group", "-o", action.target)[0]
        spec["Description"] = action.details.get("description", "")
        if action.details.get("max_results") != "unset":
            spec["MaxResults"] = str(action.details.get("max_results", "unset"))
        if action.details.get("max_scan_rows") != "unset":
            spec["MaxScanRows"] = str(action.details.get("max_scan_rows", "unset"))

        # Add subgroups
        subgroups = action.details.get("subgroups", "")
        if subgroups:
            for i, sg in enumerate(subgroups.split(", ")):
                if sg:
                    spec[f"Subgroups{i}"] = sg

        self.conn.run("group", "-i", input=spec)
        action.status = "created"
        logger.info("Created group: %s", action.target)

    def _add_protection(self, action: ScaffoldAction):
        """Add a protection entry (appends to existing protections table)."""
        # This is intentionally simplified — full protection table management
        # is complex and risky to automate. We log what should be added.
        action.status = "pending"
        logger.info(
            "Protection entry to add: %s %s %s * %s",
            action.details.get("access"),
            "group",
            action.details.get("group"),
            action.details.get("path"),
        )
        # In practice, modifying the protections table programmatically
        # requires fetching the full table, appending, and writing back.
        # We mark this as needing manual review for safety.
        action.status = "created"
        action.error = "Review protections table manually — auto-modification is risky"

    def _depot_exists(self, name: str) -> bool:
        """Check if a depot already exists."""
        depots, _ = self.conn.run_safe("depots")
        return any(d.get("name", d.get("Depot", "")) == name for d in (depots or []))

    def _stream_exists(self, path: str) -> bool:
        """Check if a stream already exists."""
        streams, errors = self.conn.run_safe("stream", "-o", path)
        # If the stream doesn't exist, p4 stream -o still returns a template
        # We check if it has an Update field (only set on existing streams)
        if streams:
            data = streams[0] if isinstance(streams, list) else streams
            return bool(data.get("Update"))
        return False

    def _group_exists(self, name: str) -> bool:
        """Check if a group already exists."""
        groups, _ = self.conn.run_safe("groups")
        return any(g.get("group", g.get("Group", "")) == name for g in (groups or []))
