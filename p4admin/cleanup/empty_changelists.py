"""
Empty Changelist Pruner.

Finds and deletes pending changelists that have no files open and
no shelved content. These accumulate over time from:
- Artists who opened a changelist, then reverted everything
- Automated tools that create changelists but don't clean up
- Failed submissions that left empty pending CLs behind
- Workspace deletions that orphaned their changelists

They clutter `p4 changes` output and make it harder to find
actual pending work.

Usage:
    pruner = EmptyChangelistPruner(p4_conn)
    report = pruner.analyze()
    render_terminal(report)

    pruner.cleanup(dry_run=True)   # Preview
    pruner.cleanup(dry_run=False)  # Delete empty changelists
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from p4admin.core.connection import P4Connection
from p4admin.core.output import ReportData

logger = logging.getLogger(__name__)


@dataclass
class EmptyChangeInfo:
    """Information about an empty pending changelist."""

    change_number: int
    owner: str
    client: str
    description: str
    date: datetime | None
    has_open_files: bool = False
    has_shelved_files: bool = False
    owner_exists: bool = True

    @property
    def is_truly_empty(self) -> bool:
        return not self.has_open_files and not self.has_shelved_files

    @property
    def days_old(self) -> int | None:
        if self.date is None:
            return None
        return (datetime.now() - self.date).days


class EmptyChangelistPruner:
    """
    Finds and removes empty pending changelists.

    Args:
        conn: Active P4Connection instance.
        min_age_days: Only consider changelists older than this (avoid deleting active work).
        exclude_users: List of usernames to skip (service accounts, etc.).
    """

    def __init__(
        self,
        conn: P4Connection,
        min_age_days: int = 7,
        exclude_users: list[str] | None = None,
    ):
        self.conn = conn
        self.min_age_days = min_age_days
        self.exclude_users = set(exclude_users or [])
        self._cutoff_date = datetime.now() - timedelta(days=min_age_days)

    def analyze(self) -> ReportData:
        """Analyze all pending changelists for empty ones."""
        logger.info(
            "Analyzing pending changelists (min age: %d days)",
            self.min_age_days,
        )

        pending = self._get_pending_changes()
        active_users = {u["User"] for u in self.conn.run("users")}

        empty_changes: list[EmptyChangeInfo] = []
        checked = 0

        for change in pending:
            owner = change.get("user", "")
            if owner in self.exclude_users:
                continue

            info = self._analyze_changelist(change, active_users)
            checked += 1

            if info.is_truly_empty:
                # Only include if old enough
                if info.date and info.date < self._cutoff_date:
                    empty_changes.append(info)

        # Group by owner
        by_owner: dict[str, list[EmptyChangeInfo]] = {}
        for cl in empty_changes:
            by_owner.setdefault(cl.owner, []).append(cl)

        report = ReportData(
            title="Empty Changelist Report",
            server_info=self.conn.server_info,
            summary={
                "Total pending changelists": len(pending),
                "Changelists checked": checked,
                "Empty changelists": len(empty_changes),
                "Affected users": len(by_owner),
                "Minimum age threshold": f"{self.min_age_days} days",
            },
        )

        if empty_changes:
            report.add_section(
                title="Empty Pending Changelists",
                headers=["Change", "Owner", "Client", "Age (days)", "Description"],
                rows=[
                    [
                        str(cl.change_number),
                        cl.owner,
                        cl.client,
                        str(cl.days_old) if cl.days_old is not None else "N/A",
                        cl.description[:60] + ("..." if len(cl.description) > 60 else ""),
                    ]
                    for cl in sorted(empty_changes, key=lambda x: x.days_old or 0, reverse=True)
                ],
                notes=[
                    "These changelists have no open files and no shelved content.",
                    f"Only showing changelists older than {self.min_age_days} days.",
                ],
            )

        # Per-user summary
        if by_owner:
            report.add_section(
                title="Empty Changelists by User",
                headers=["User", "Empty CLs", "Oldest (days)"],
                rows=[
                    [
                        owner,
                        str(len(cls)),
                        str(max((cl.days_old or 0) for cl in cls)),
                    ]
                    for owner, cls in sorted(by_owner.items(), key=lambda x: len(x[1]), reverse=True)
                ],
            )

        return report

    def cleanup(self, dry_run: bool = True) -> list[int]:
        """
        Delete empty pending changelists.

        Args:
            dry_run: If True, only report what would be deleted.

        Returns:
            List of changelist numbers that were (or would be) deleted.
        """
        pending = self._get_pending_changes()
        active_users = {u["User"] for u in self.conn.run("users")}

        deleted = []
        for change in pending:
            owner = change.get("user", "")
            if owner in self.exclude_users:
                continue

            info = self._analyze_changelist(change, active_users)
            if not info.is_truly_empty:
                continue
            if info.date and info.date >= self._cutoff_date:
                continue

            if dry_run:
                logger.info(
                    "[DRY RUN] Would delete empty CL %d (owner: %s, age: %s days)",
                    info.change_number, info.owner, info.days_old,
                )
            else:
                try:
                    self.conn.run("change", "-df", str(info.change_number))
                    logger.info("Deleted empty CL %d (owner: %s)", info.change_number, info.owner)
                except Exception as e:
                    logger.error("Failed to delete CL %d: %s", info.change_number, e)
                    continue
            deleted.append(info.change_number)

        action = "Would delete" if dry_run else "Deleted"
        logger.info("%s %d empty changelists", action, len(deleted))
        return deleted

    def _get_pending_changes(self) -> list[dict[str, Any]]:
        """Get all pending changelists across all users."""
        results = self.conn.run("changes", "-s", "pending", "-l")
        logger.info("Found %d pending changelists", len(results))
        return results

    def _analyze_changelist(
        self, change_data: dict[str, Any], active_users: set[str]
    ) -> EmptyChangeInfo:
        """Check if a changelist is empty."""
        change_num = int(change_data.get("change", 0))

        info = EmptyChangeInfo(
            change_number=change_num,
            owner=change_data.get("user", ""),
            client=change_data.get("client", ""),
            description=change_data.get("desc", "").strip(),
            date=self._parse_p4_date(change_data.get("time")),
            owner_exists=change_data.get("user", "") in active_users,
        )

        # Check for open files
        open_files, _ = self.conn.run_safe("opened", "-c", str(change_num))
        info.has_open_files = bool(open_files)

        # Check for shelved files
        if not info.has_open_files:
            shelved, _ = self.conn.run_safe("describe", "-sS", str(change_num))
            if shelved:
                desc = shelved[0] if isinstance(shelved, list) else shelved
                info.has_shelved_files = "depotFile0" in desc

        return info

    @staticmethod
    def _parse_p4_date(date_str: str | None) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromtimestamp(int(date_str))
        except (ValueError, TypeError):
            return None
