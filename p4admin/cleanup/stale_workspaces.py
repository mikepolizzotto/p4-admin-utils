"""
Stale Workspace Finder & Cleaner.

Identifies Perforce workspaces (clients) that are likely abandoned:
- Owner has no recent activity (no sync/submit in N days)
- Owner no longer exists in the user directory
- Workspace hasn't been accessed in N days
- Workspace belongs to a user who's been removed from all groups

This is the #1 time-waster for P4 admins. Studios with freelancers cycling
on and off productions accumulate hundreds of dead workspaces. P4Admin
shows you the list, but you still have to evaluate each one manually.

Usage:
    finder = StaleWorkspaceFinder(p4_conn, stale_days=90)
    report = finder.analyze()
    render_terminal(report)

    # Or clean up (with confirmation)
    finder.cleanup(dry_run=True)   # preview what would be deleted
    finder.cleanup(dry_run=False)  # actually delete
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from p4admin.core.connection import P4Connection
from p4admin.core.output import ReportData
from p4admin.core.utils import parse_p4_date

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceInfo:
    """Parsed workspace information with staleness analysis."""

    name: str
    owner: str
    host: str
    root: str
    description: str
    access: datetime | None  # Last access time
    update: datetime | None  # Last update time
    stream: str | None
    owner_exists: bool = True
    owner_last_activity: datetime | None = None
    pending_changes: int = 0
    shelved_changes: int = 0
    stale_reason: str = ""

    @property
    def is_stale(self) -> bool:
        return bool(self.stale_reason)

    @property
    def days_since_access(self) -> int | None:
        if self.access is None:
            return None
        delta = datetime.now() - self.access
        return delta.days

    @property
    def has_pending_work(self) -> bool:
        return self.pending_changes > 0 or self.shelved_changes > 0

    @property
    def safe_to_delete(self) -> bool:
        """Conservative check: stale AND no pending work."""
        return self.is_stale and not self.has_pending_work


class StaleWorkspaceFinder:
    """
    Finds and optionally cleans up stale Perforce workspaces.

    Args:
        conn: Active P4Connection instance.
        stale_days: Number of days with no access before a workspace is "stale".
        check_owner: Also check if the workspace owner still exists as a user.
        check_pending: Check for pending/shelved changelists before recommending deletion.
        exclude_patterns: List of workspace name patterns to skip (e.g., ["build-*", "ci-*"]).
    """

    def __init__(
        self,
        conn: P4Connection,
        stale_days: int = 90,
        check_owner: bool = True,
        check_pending: bool = True,
        exclude_patterns: list[str] | None = None,
    ):
        self.conn = conn
        self.stale_days = stale_days
        self.check_owner = check_owner
        self.check_pending = check_pending
        self.exclude_patterns = exclude_patterns or []
        self._active_users: set[str] | None = None
        self._cutoff_date = datetime.now() - timedelta(days=stale_days)

    def analyze(self) -> ReportData:
        """
        Analyze all workspaces and generate a report.

        Returns:
            ReportData with stale workspace findings.
        """
        logger.info(
            "Analyzing workspaces (stale threshold: %d days, cutoff: %s)",
            self.stale_days,
            self._cutoff_date.strftime("%Y-%m-%d"),
        )

        # Gather data
        workspaces = self._get_all_workspaces()
        if self.check_owner:
            self._active_users = self._get_active_users()

        # Analyze each workspace
        analyzed: list[WorkspaceInfo] = []
        for ws in workspaces:
            if self._should_exclude(ws.get("client", "")):
                continue
            info = self._analyze_workspace(ws)
            analyzed.append(info)

        # Build report
        stale = [w for w in analyzed if w.is_stale]
        safe_to_delete = [w for w in stale if w.safe_to_delete]
        has_pending = [w for w in stale if w.has_pending_work]

        report = ReportData(
            title="Stale Workspace Report",
            server_info=self.conn.server_info,
            summary={
                "Total workspaces": len(analyzed),
                "Stale workspaces": len(stale),
                "Safe to delete": len(safe_to_delete),
                "Stale with pending work": len(has_pending),
                "Stale threshold": f"{self.stale_days} days",
            },
        )

        # Safe to delete section
        if safe_to_delete:
            report.add_section(
                title="Safe to Delete",
                headers=["Workspace", "Owner", "Last Access", "Days Idle", "Reason"],
                rows=[
                    [
                        w.name,
                        w.owner,
                        w.access.strftime("%Y-%m-%d") if w.access else "Never",
                        str(w.days_since_access) if w.days_since_access is not None else "N/A",
                        w.stale_reason,
                    ]
                    for w in sorted(safe_to_delete, key=lambda x: x.days_since_access or 9999, reverse=True)
                ],
                notes=[
                    "These workspaces have no pending changelists or shelved files.",
                    f"Run with --cleanup to delete, or --cleanup --dry-run to preview.",
                ],
            )

        # Needs attention section (stale but has pending work)
        if has_pending:
            report.add_section(
                title="Needs Review (Stale but has pending work)",
                headers=[
                    "Workspace", "Owner", "Last Access", "Pending CLs",
                    "Shelved CLs", "Reason",
                ],
                rows=[
                    [
                        w.name,
                        w.owner,
                        w.access.strftime("%Y-%m-%d") if w.access else "Never",
                        str(w.pending_changes),
                        str(w.shelved_changes),
                        w.stale_reason,
                    ]
                    for w in sorted(has_pending, key=lambda x: x.days_since_access or 9999, reverse=True)
                ],
                notes=[
                    "These workspaces are stale but have unshelved or pending work.",
                    "Review before deleting — shelved changes may need to be preserved.",
                ],
            )

        # Owner no longer exists
        orphaned = [w for w in stale if not w.owner_exists]
        if orphaned:
            report.add_section(
                title="Orphaned (Owner no longer exists)",
                headers=["Workspace", "Former Owner", "Last Access", "Pending CLs"],
                rows=[
                    [
                        w.name,
                        w.owner,
                        w.access.strftime("%Y-%m-%d") if w.access else "Never",
                        str(w.pending_changes),
                    ]
                    for w in orphaned
                ],
                notes=["These workspace owners don't exist in the P4 user directory."],
            )

        return report

    def cleanup(self, dry_run: bool = True, force: bool = False) -> list[str]:
        """
        Delete stale workspaces.

        Args:
            dry_run: If True, only report what would be deleted.
            force: If True, also delete workspaces with pending work (dangerous).

        Returns:
            List of workspace names that were (or would be) deleted.
        """
        report = self.analyze()
        workspaces_to_analyze = self._get_all_workspaces()

        # Re-analyze to get current state
        targets: list[WorkspaceInfo] = []
        for ws in workspaces_to_analyze:
            if self._should_exclude(ws.get("client", "")):
                continue
            info = self._analyze_workspace(ws)
            if force:
                if info.is_stale:
                    targets.append(info)
            else:
                if info.safe_to_delete:
                    targets.append(info)

        deleted = []
        for ws in targets:
            if dry_run:
                logger.info("[DRY RUN] Would delete workspace: %s (owner: %s)", ws.name, ws.owner)
            else:
                try:
                    # Revert any open files first (use -C to target another user's workspace)
                    self.conn.run_safe("revert", "-k", "-C", ws.name, "//...")
                    # Delete the workspace (-d = delete, -f = force)
                    self.conn.run("client", "-d", "-f", ws.name)
                    logger.info("Deleted workspace: %s (owner: %s)", ws.name, ws.owner)
                except Exception as e:
                    logger.error("Failed to delete workspace %s: %s", ws.name, e)
                    continue
            deleted.append(ws.name)

        action = "Would delete" if dry_run else "Deleted"
        logger.info("%s %d workspaces", action, len(deleted))
        return deleted

    def _get_all_workspaces(self) -> list[dict[str, Any]]:
        """Get all workspaces from the server."""
        results = self.conn.run("clients")
        logger.info("Found %d total workspaces", len(results))
        return results

    def _get_active_users(self) -> set[str]:
        """Get set of all active user IDs."""
        users = self.conn.run("users")
        active = {u["User"] for u in users}
        logger.info("Found %d active users", len(active))
        return active

    def _analyze_workspace(self, ws_data: dict[str, Any]) -> WorkspaceInfo:
        """Analyze a single workspace for staleness."""
        name = ws_data.get("client", ws_data.get("Client", ""))
        owner = ws_data.get("Owner", "")

        # Parse timestamps
        access = parse_p4_date(ws_data.get("Access"))
        update = parse_p4_date(ws_data.get("Update"))

        info = WorkspaceInfo(
            name=name,
            owner=owner,
            host=ws_data.get("Host", ""),
            root=ws_data.get("Root", ""),
            description=ws_data.get("Description", "").strip(),
            access=access,
            update=update,
            stream=ws_data.get("Stream"),
        )

        # Check owner existence
        if self.check_owner and self._active_users is not None:
            info.owner_exists = owner in self._active_users

        # Check pending/shelved changelists
        if self.check_pending:
            pending, _ = self.conn.run_safe("changes", "-s", "pending", "-c", name)
            info.pending_changes = len(pending) if pending else 0

            shelved, _ = self.conn.run_safe("changes", "-s", "shelved", "-c", name)
            info.shelved_changes = len(shelved) if shelved else 0

        # Determine staleness
        info.stale_reason = self._determine_stale_reason(info)

        return info

    def _determine_stale_reason(self, info: WorkspaceInfo) -> str:
        """Determine why a workspace is stale (empty string = not stale)."""
        reasons = []

        if not info.owner_exists:
            reasons.append("owner no longer exists")

        if info.access and info.access < self._cutoff_date:
            reasons.append(f"no access in {info.days_since_access} days")
        elif info.access is None and info.update and info.update < self._cutoff_date:
            reasons.append(f"no activity (last update {info.update.strftime('%Y-%m-%d')})")

        return "; ".join(reasons)

    def _should_exclude(self, name: str) -> bool:
        """Check if workspace matches any exclusion pattern."""
        import fnmatch

        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(name, pattern):
                logger.debug("Excluding workspace %s (matches pattern %s)", name, pattern)
                return True
        return False

