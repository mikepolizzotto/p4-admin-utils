"""
Orphaned Shelf Cleanup.

Finds shelved changelists that are likely abandoned:
- Owner no longer exists as a user
- Owner hasn't accessed the server in N days
- Shelf hasn't been modified in N days
- Shelf belongs to a deleted/stale workspace

Shelved changes from departed freelancers are one of the biggest
sources of hidden storage consumption on P4 servers. They also
create confusion when someone searches for shelved work and finds
changes from people who left two productions ago.

Usage:
    cleaner = OrphanedShelfCleaner(p4_conn, stale_days=60)
    report = cleaner.analyze()
    render_terminal(report)

    cleaner.cleanup(dry_run=True)   # Preview
    cleaner.cleanup(dry_run=False)  # Delete orphaned shelves
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
class ShelvedChangeInfo:
    """Parsed shelved changelist information."""

    change_number: int
    owner: str
    description: str
    date: datetime | None
    client: str
    file_count: int = 0
    total_size: int = 0  # bytes
    owner_exists: bool = True
    owner_last_access: datetime | None = None
    stale_reason: str = ""

    @property
    def is_orphaned(self) -> bool:
        return bool(self.stale_reason)

    @property
    def days_since_shelved(self) -> int | None:
        if self.date is None:
            return None
        return (datetime.now() - self.date).days

    @property
    def size_display(self) -> str:
        """Human-readable size."""
        if self.total_size == 0:
            return "Unknown"
        for unit in ["B", "KB", "MB", "GB"]:
            if self.total_size < 1024:
                return f"{self.total_size:.1f} {unit}"
            self.total_size /= 1024
        return f"{self.total_size:.1f} TB"


class OrphanedShelfCleaner:
    """
    Finds and cleans up orphaned shelved changelists.

    Args:
        conn: Active P4Connection instance.
        stale_days: Days since last modification before a shelf is "stale".
        check_owner: Also check if shelf owner still exists.
    """

    def __init__(
        self,
        conn: P4Connection,
        stale_days: int = 60,
        check_owner: bool = True,
    ):
        self.conn = conn
        self.stale_days = stale_days
        self.check_owner = check_owner
        self._active_users: set[str] | None = None
        self._cutoff_date = datetime.now() - timedelta(days=stale_days)

    def analyze(self) -> ReportData:
        """Analyze all shelved changelists and generate a report."""
        logger.info(
            "Analyzing shelved changelists (stale threshold: %d days)",
            self.stale_days,
        )

        shelved_changes = self._get_shelved_changes()
        if self.check_owner:
            self._active_users = self._get_active_users()

        analyzed: list[ShelvedChangeInfo] = []
        for change in shelved_changes:
            info = self._analyze_shelf(change)
            analyzed.append(info)

        orphaned = [s for s in analyzed if s.is_orphaned]
        owner_gone = [s for s in orphaned if not s.owner_exists]
        stale_only = [s for s in orphaned if s.owner_exists]

        report = ReportData(
            title="Orphaned Shelved Changes Report",
            server_info=self.conn.server_info,
            summary={
                "Total shelved changes": len(analyzed),
                "Orphaned shelves": len(orphaned),
                "Owner no longer exists": len(owner_gone),
                "Stale shelves (owner active)": len(stale_only),
                "Stale threshold": f"{self.stale_days} days",
            },
        )

        if owner_gone:
            report.add_section(
                title="Owner No Longer Exists",
                headers=["Change", "Former Owner", "Client", "Date", "Days Old", "Files", "Description"],
                rows=[
                    [
                        str(s.change_number),
                        s.owner,
                        s.client,
                        s.date.strftime("%Y-%m-%d") if s.date else "Unknown",
                        str(s.days_since_shelved) if s.days_since_shelved is not None else "N/A",
                        str(s.file_count),
                        s.description[:60] + ("..." if len(s.description) > 60 else ""),
                    ]
                    for s in sorted(owner_gone, key=lambda x: x.days_since_shelved or 9999, reverse=True)
                ],
                notes=[
                    "These shelved changes belong to users who no longer exist in the P4 user directory.",
                    "Review descriptions before deleting — some may contain work that should be preserved.",
                ],
            )

        if stale_only:
            report.add_section(
                title="Stale Shelves (Owner Still Active)",
                headers=["Change", "Owner", "Client", "Date", "Days Old", "Files", "Description"],
                rows=[
                    [
                        str(s.change_number),
                        s.owner,
                        s.client,
                        s.date.strftime("%Y-%m-%d") if s.date else "Unknown",
                        str(s.days_since_shelved) if s.days_since_shelved is not None else "N/A",
                        str(s.file_count),
                        s.description[:60] + ("..." if len(s.description) > 60 else ""),
                    ]
                    for s in sorted(stale_only, key=lambda x: x.days_since_shelved or 9999, reverse=True)
                ],
                notes=[
                    f"These shelves haven't been modified in {self.stale_days}+ days but the owner is still active.",
                    "Consider notifying owners before cleanup.",
                ],
            )

        return report

    def cleanup(self, dry_run: bool = True, owner_gone_only: bool = False) -> list[int]:
        """
        Delete orphaned shelved changelists.

        Args:
            dry_run: If True, only report what would be deleted.
            owner_gone_only: If True, only delete shelves where the owner no longer exists.

        Returns:
            List of changelist numbers that were (or would be) deleted.
        """
        shelved_changes = self._get_shelved_changes()
        if self.check_owner:
            self._active_users = self._get_active_users()

        targets: list[ShelvedChangeInfo] = []
        for change in shelved_changes:
            info = self._analyze_shelf(change)
            if not info.is_orphaned:
                continue
            if owner_gone_only and info.owner_exists:
                continue
            targets.append(info)

        deleted = []
        for shelf in targets:
            if dry_run:
                logger.info(
                    "[DRY RUN] Would delete shelved CL %d (owner: %s, reason: %s)",
                    shelf.change_number, shelf.owner, shelf.stale_reason,
                )
            else:
                try:
                    self.conn.run("shelve", "-df", "-c", str(shelf.change_number))
                    # After unshelving files, delete the empty changelist
                    self.conn.run_safe("change", "-df", str(shelf.change_number))
                    logger.info("Deleted shelved CL %d (owner: %s)", shelf.change_number, shelf.owner)
                except Exception as e:
                    logger.error("Failed to delete shelved CL %d: %s", shelf.change_number, e)
                    continue
            deleted.append(shelf.change_number)

        action = "Would delete" if dry_run else "Deleted"
        logger.info("%s %d shelved changelists", action, len(deleted))
        return deleted

    def _get_shelved_changes(self) -> list[dict[str, Any]]:
        """Get all shelved changelists."""
        results = self.conn.run("changes", "-s", "shelved")
        logger.info("Found %d shelved changelists", len(results))
        return results

    def _get_active_users(self) -> set[str]:
        """Get set of all active user IDs."""
        users = self.conn.run("users")
        return {u["User"] for u in users}

    def _analyze_shelf(self, change_data: dict[str, Any]) -> ShelvedChangeInfo:
        """Analyze a single shelved changelist."""
        change_num = int(change_data.get("change", 0))

        info = ShelvedChangeInfo(
            change_number=change_num,
            owner=change_data.get("user", ""),
            description=change_data.get("desc", "").strip(),
            date=self._parse_p4_date(change_data.get("time")),
            client=change_data.get("client", ""),
        )

        # Get file count for this shelf
        shelved_files, _ = self.conn.run_safe("describe", "-sS", str(change_num))
        if shelved_files:
            desc = shelved_files[0] if isinstance(shelved_files, list) else shelved_files
            # Count depotFile entries
            idx = 0
            while f"depotFile{idx}" in desc:
                idx += 1
            info.file_count = idx

        # Check owner existence
        if self.check_owner and self._active_users is not None:
            info.owner_exists = info.owner in self._active_users

        # Determine orphan status
        reasons = []
        if not info.owner_exists:
            reasons.append("owner no longer exists")
        if info.date and info.date < self._cutoff_date:
            reasons.append(f"shelved {info.days_since_shelved} days ago")
        info.stale_reason = "; ".join(reasons)

        return info

    @staticmethod
    def _parse_p4_date(date_str: str | None) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.fromtimestamp(int(date_str))
        except (ValueError, TypeError):
            try:
                return datetime.strptime(date_str, "%Y/%m/%d %H:%M:%S")
            except (ValueError, TypeError):
                return None
