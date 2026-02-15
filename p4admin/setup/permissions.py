"""
Permission Group Templates.

Defines standard Perforce group structures mapped to production roles.
Every studio has the same basic hierarchy:
- Artists who need read/write to their department's area
- Leads who need broader write access
- TDs/Pipeline who need admin-level access for tooling
- Producers/coordinators who need read access everywhere
- Production admins who manage the P4 infrastructure

Instead of reinventing this every production, define it once and
apply it with role-based templates.

Usage:
    manager = PermissionTemplateManager(p4_conn)

    # List available role templates
    templates = manager.list_templates()

    # Preview what groups would be created
    report = manager.preview("film", production_name="titan")

    # Create groups
    manager.apply("film", production_name="titan", dry_run=False)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from p4admin.core.connection import P4Connection
from p4admin.core.output import ReportData

logger = logging.getLogger(__name__)


@dataclass
class GroupSpec:
    """Specification for a P4 group to create."""

    name_template: str  # e.g., "{prod}-artists" — {prod} gets replaced
    description_template: str
    max_results: int | str = "unset"
    max_scan_rows: int | str = "unset"
    timeout: int | str = "unset"
    password_timeout: int | str = "unset"
    subgroups: list[str] = field(default_factory=list)  # Templates for subgroup names
    access_level: str = ""  # For documentation: "read", "write", "admin"
    notes: str = ""


# Role-based group templates for different production types.
# {prod} is replaced with the production name at apply time.

PERMISSION_TEMPLATES: dict[str, dict[str, Any]] = {
    "film": {
        "description": "Animation/VFX film production roles",
        "groups": [
            GroupSpec(
                name_template="{prod}-artists",
                description_template="{prod} — Artists (read/write to department areas)",
                max_results=10000,
                max_scan_rows=100000,
                access_level="write (department)",
                notes="Base group for all artists on the production",
            ),
            GroupSpec(
                name_template="{prod}-leads",
                description_template="{prod} — Department Leads (broad write access)",
                max_results=50000,
                max_scan_rows=500000,
                subgroups=["{prod}-artists"],
                access_level="write (production-wide)",
                notes="Leads can write across department boundaries",
            ),
            GroupSpec(
                name_template="{prod}-pipeline",
                description_template="{prod} — Pipeline/TDs (admin access for tooling)",
                max_results=100000,
                max_scan_rows=1000000,
                access_level="admin (tooling)",
                notes="TDs need elevated access for pipeline tools and triggers",
            ),
            GroupSpec(
                name_template="{prod}-coordinators",
                description_template="{prod} — Producers/Coordinators (read access)",
                max_results=10000,
                max_scan_rows=50000,
                access_level="read",
                notes="Read-only access for production tracking",
            ),
            GroupSpec(
                name_template="{prod}-admins",
                description_template="{prod} — Production P4 Admins",
                max_results="unset",
                max_scan_rows="unset",
                subgroups=["{prod}-leads", "{prod}-pipeline"],
                access_level="admin",
                notes="Full admin access for production infrastructure",
            ),
        ],
    },
    "games": {
        "description": "Game development production roles",
        "groups": [
            GroupSpec(
                name_template="{prod}-content",
                description_template="{prod} — Content creators (artists, designers, audio)",
                max_results=25000,
                max_scan_rows=250000,
                access_level="write (content)",
                notes="Artists, level designers, audio — content creation roles",
            ),
            GroupSpec(
                name_template="{prod}-engineering",
                description_template="{prod} — Engineers (code and engine areas)",
                max_results=50000,
                max_scan_rows=500000,
                access_level="write (code)",
                notes="Programmers, engine team, tools engineers",
            ),
            GroupSpec(
                name_template="{prod}-qa",
                description_template="{prod} — QA team (read + limited write for test assets)",
                max_results=10000,
                max_scan_rows=100000,
                access_level="read + limited write",
                notes="QA needs read access everywhere, write to test areas",
            ),
            GroupSpec(
                name_template="{prod}-leads",
                description_template="{prod} — Department leads and directors",
                max_results=50000,
                max_scan_rows=500000,
                subgroups=["{prod}-content", "{prod}-engineering"],
                access_level="write (production-wide)",
            ),
            GroupSpec(
                name_template="{prod}-build",
                description_template="{prod} — Build/CI systems (service accounts)",
                max_results="unset",
                max_scan_rows="unset",
                timeout=0,  # No timeout for service accounts
                access_level="write (build outputs)",
                notes="Service accounts for CI/CD pipelines",
            ),
            GroupSpec(
                name_template="{prod}-admins",
                description_template="{prod} — Production P4 admins",
                max_results="unset",
                max_scan_rows="unset",
                subgroups=["{prod}-leads"],
                access_level="admin",
            ),
        ],
    },
    "post": {
        "description": "Post-production & Editorial roles",
        "groups": [
            GroupSpec(
                name_template="{prod}-editorial",
                description_template="{prod} — Editors and assistants",
                max_results=10000,
                max_scan_rows=100000,
                access_level="write (editorial)",
                notes="Editors, assistant editors, editorial team",
            ),
            GroupSpec(
                name_template="{prod}-conform",
                description_template="{prod} — Conform/online artists",
                max_results=25000,
                max_scan_rows=250000,
                access_level="write (conform)",
            ),
            GroupSpec(
                name_template="{prod}-color",
                description_template="{prod} — Colorists and DI team",
                max_results=25000,
                max_scan_rows=250000,
                access_level="write (color)",
            ),
            GroupSpec(
                name_template="{prod}-producers",
                description_template="{prod} — Producers and post supervisors",
                max_results=10000,
                max_scan_rows=50000,
                subgroups=["{prod}-editorial"],
                access_level="read",
            ),
            GroupSpec(
                name_template="{prod}-admins",
                description_template="{prod} — Post P4 admins",
                max_results="unset",
                max_scan_rows="unset",
                access_level="admin",
            ),
        ],
    },
}


class PermissionTemplateManager:
    """
    Manages role-based permission group templates.

    Args:
        conn: Active P4Connection instance.
    """

    def __init__(self, conn: P4Connection):
        self.conn = conn

    def list_templates(self) -> dict[str, str]:
        """Return available template names and descriptions."""
        return {name: tmpl["description"] for name, tmpl in PERMISSION_TEMPLATES.items()}

    def preview(self, template_name: str, production_name: str) -> ReportData:
        """Preview what groups would be created."""
        template = self._get_template(template_name)
        groups = self._resolve_groups(template, production_name)

        # Check which already exist
        existing_groups = self._get_existing_groups()
        new_groups = [g for g in groups if g["name"] not in existing_groups]
        existing = [g for g in groups if g["name"] in existing_groups]

        report = ReportData(
            title=f"Permission Template Preview: {template_name}",
            server_info=self.conn.server_info,
            summary={
                "Template": template_name,
                "Production": production_name,
                "Total groups": len(groups),
                "New groups": len(new_groups),
                "Already exist": len(existing),
            },
        )

        report.add_section(
            title="Groups to Create",
            headers=["Group Name", "Access Level", "Subgroups", "Max Results", "Notes"],
            rows=[
                [
                    g["name"],
                    g["access_level"],
                    g.get("subgroups_display", ""),
                    str(g["max_results"]),
                    g.get("notes", ""),
                ]
                for g in groups
            ],
        )

        return report

    def apply(self, template_name: str, production_name: str, dry_run: bool = True) -> ReportData:
        """
        Create permission groups from a template.

        Args:
            template_name: Template to apply.
            production_name: Production name (replaces {prod} in templates).
            dry_run: If True, only preview.
        """
        template = self._get_template(template_name)
        groups = self._resolve_groups(template, production_name)
        existing_groups = self._get_existing_groups()

        created = 0
        skipped = 0
        failed = 0
        results = []

        for group in groups:
            if group["name"] in existing_groups:
                results.append((group["name"], "EXISTS"))
                skipped += 1
                continue

            if dry_run:
                results.append((group["name"], "WOULD CREATE"))
                created += 1
            else:
                try:
                    self._create_group(group)
                    results.append((group["name"], "CREATED"))
                    created += 1
                except Exception as e:
                    results.append((group["name"], f"FAILED: {e}"))
                    failed += 1

        report = ReportData(
            title=f"Permission Groups: {production_name}" + (" [DRY RUN]" if dry_run else ""),
            server_info=self.conn.server_info,
            summary={
                "Production": production_name,
                "Template": template_name,
                "Created": created,
                "Skipped (exist)": skipped,
                "Failed": failed,
            },
        )

        report.add_section(
            title="Results",
            headers=["Group", "Status"],
            rows=[[name, status] for name, status in results],
        )

        return report

    def _get_template(self, template_name: str) -> dict[str, Any]:
        """Get a template by name."""
        if template_name not in PERMISSION_TEMPLATES:
            available = ", ".join(PERMISSION_TEMPLATES.keys())
            raise ValueError(f"Unknown template '{template_name}'. Available: {available}")
        return PERMISSION_TEMPLATES[template_name]

    def _resolve_groups(self, template: dict[str, Any], production_name: str) -> list[dict[str, Any]]:
        """Resolve template placeholders with actual production name."""
        resolved = []
        for spec in template["groups"]:
            group = {
                "name": spec.name_template.replace("{prod}", production_name),
                "description": spec.description_template.replace("{prod}", production_name),
                "max_results": spec.max_results,
                "max_scan_rows": spec.max_scan_rows,
                "timeout": spec.timeout,
                "password_timeout": spec.password_timeout,
                "subgroups": [sg.replace("{prod}", production_name) for sg in spec.subgroups],
                "access_level": spec.access_level,
                "notes": spec.notes,
            }
            group["subgroups_display"] = ", ".join(group["subgroups"]) if group["subgroups"] else ""
            resolved.append(group)
        return resolved

    def _get_existing_groups(self) -> set[str]:
        """Get set of existing group names."""
        groups, _ = self.conn.run_safe("groups")
        return {g.get("group", g.get("Group", "")) for g in (groups or [])}

    def _create_group(self, group: dict[str, Any]):
        """Create a single P4 group."""
        spec = self.conn.run("group", "-o", group["name"])[0]
        spec["Description"] = group["description"]

        if group["max_results"] != "unset":
            spec["MaxResults"] = str(group["max_results"])
        if group["max_scan_rows"] != "unset":
            spec["MaxScanRows"] = str(group["max_scan_rows"])
        if group["timeout"] != "unset":
            spec["Timeout"] = str(group["timeout"])

        # Add subgroups
        for i, sg in enumerate(group.get("subgroups", [])):
            spec[f"Subgroups{i}"] = sg

        self.conn.run("group", "-i", input=spec)
        logger.info("Created group: %s", group["name"])
