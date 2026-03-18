"""
User Activity Report.

Gives you visibility into who's actually using Perforce and how.
Useful for:
- License seat optimization (who hasn't submitted in months?)
- Production staffing visibility (which team is most active?)
- Identifying power users vs. occasional users
- Onboarding/offboarding audits (new users who've never synced)

Usage:
    reporter = UserActivityReport(p4_conn)
    report = reporter.analyze()
    render_terminal(report)

    # Focus on last 30 days
    report = reporter.analyze(days=30)
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
class UserActivity:
    """Activity summary for a single user."""

    username: str
    full_name: str
    email: str
    last_access: datetime | None = None
    workspace_count: int = 0
    pending_changes: int = 0
    shelved_changes: int = 0
    recent_submits: int = 0  # Submits within the analysis window
    total_submits: int = 0
    groups: list[str] = field(default_factory=list)

    @property
    def days_since_access(self) -> int | None:
        if self.last_access is None:
            return None
        return (datetime.now() - self.last_access).days

    @property
    def activity_level(self) -> str:
        """Categorize user activity level."""
        if self.days_since_access is None:
            return "Never accessed"
        if self.days_since_access <= 7:
            return "Active"
        if self.days_since_access <= 30:
            return "Recent"
        if self.days_since_access <= 90:
            return "Infrequent"
        return "Inactive"


class UserActivityReport:
    """
    Generates a comprehensive user activity report.

    Args:
        conn: Active P4Connection instance.
        days: Analysis window in days (for "recent" activity metrics).
        include_groups: Also fetch group membership for each user.
    """

    def __init__(
        self,
        conn: P4Connection,
        days: int = 90,
        include_groups: bool = True,
    ):
        self.conn = conn
        self.days = days
        self.include_groups = include_groups
        self._cutoff = datetime.now() - timedelta(days=days)

    def analyze(self) -> ReportData:
        """Analyze all user activity and generate a report."""
        logger.info("Analyzing user activity (window: %d days)", self.days)

        users = self.conn.run("users")
        logger.info("Found %d users", len(users))

        # Get group membership if requested
        group_map: dict[str, list[str]] = {}
        if self.include_groups:
            group_map = self._get_user_groups()

        # Analyze each user
        activities: list[UserActivity] = []
        for user_data in users:
            activity = self._analyze_user(user_data, group_map)
            activities.append(activity)

        # Categorize
        active = [u for u in activities if u.activity_level == "Active"]
        recent = [u for u in activities if u.activity_level == "Recent"]
        infrequent = [u for u in activities if u.activity_level == "Infrequent"]
        inactive = [u for u in activities if u.activity_level == "Inactive"]
        never = [u for u in activities if u.activity_level == "Never accessed"]

        report = ReportData(
            title="User Activity Report",
            server_info=self.conn.server_info,
            summary={
                "Total users": len(activities),
                "Active (last 7 days)": len(active),
                "Recent (8-30 days)": len(recent),
                "Infrequent (31-90 days)": len(infrequent),
                "Inactive (90+ days)": len(inactive),
                "Never accessed": len(never),
                "Analysis window": f"{self.days} days",
            },
        )

        # Active users with their stats
        if active:
            report.add_section(
                title="Active Users (last 7 days)",
                headers=["User", "Full Name", "Last Access", "Workspaces", "Recent Submits", "Pending CLs"],
                rows=[
                    [
                        u.username,
                        u.full_name,
                        u.last_access.strftime("%Y-%m-%d") if u.last_access else "N/A",
                        str(u.workspace_count),
                        str(u.recent_submits),
                        str(u.pending_changes),
                    ]
                    for u in sorted(active, key=lambda x: x.recent_submits, reverse=True)
                ],
            )

        # Inactive users — these are the ones costing you seats
        if inactive:
            report.add_section(
                title="Inactive Users (90+ days)",
                headers=["User", "Full Name", "Email", "Last Access", "Days Idle", "Workspaces", "Groups"],
                rows=[
                    [
                        u.username,
                        u.full_name,
                        u.email,
                        u.last_access.strftime("%Y-%m-%d") if u.last_access else "N/A",
                        str(u.days_since_access) if u.days_since_access is not None else "N/A",
                        str(u.workspace_count),
                        ", ".join(u.groups[:3]) + ("..." if len(u.groups) > 3 else ""),
                    ]
                    for u in sorted(inactive, key=lambda x: x.days_since_access or 9999, reverse=True)
                ],
                notes=[
                    f"These {len(inactive)} users haven't accessed the server in 90+ days.",
                    "Consider deactivating to free license seats.",
                ],
            )

        # Never accessed — likely onboarding issues
        if never:
            report.add_section(
                title="Never Accessed (possible onboarding issues)",
                headers=["User", "Full Name", "Email", "Groups"],
                rows=[
                    [
                        u.username,
                        u.full_name,
                        u.email,
                        ", ".join(u.groups[:3]) + ("..." if len(u.groups) > 3 else ""),
                    ]
                    for u in never
                ],
                notes=[
                    "These users exist but have never connected to the server.",
                    "May indicate incomplete onboarding or provisioned-but-unused accounts.",
                ],
            )

        # Top submitters
        top_submitters = sorted(activities, key=lambda u: u.recent_submits, reverse=True)[:15]
        if top_submitters and any(u.recent_submits > 0 for u in top_submitters):
            report.add_section(
                title=f"Top Submitters (last {self.days} days)",
                headers=["User", "Full Name", "Submits", "Pending CLs", "Shelved CLs"],
                rows=[
                    [
                        u.username,
                        u.full_name,
                        str(u.recent_submits),
                        str(u.pending_changes),
                        str(u.shelved_changes),
                    ]
                    for u in top_submitters
                    if u.recent_submits > 0
                ],
            )

        return report

    def _analyze_user(
        self, user_data: dict[str, Any], group_map: dict[str, list[str]]
    ) -> UserActivity:
        """Analyze activity for a single user."""
        username = user_data.get("User", "")

        activity = UserActivity(
            username=username,
            full_name=user_data.get("FullName", ""),
            email=user_data.get("Email", ""),
            groups=group_map.get(username, []),
        )

        # Parse last access
        activity.last_access = parse_p4_date(user_data.get("Access"))

        # Count workspaces
        clients, _ = self.conn.run_safe("clients", "-u", username)
        activity.workspace_count = len(clients) if clients else 0

        # Count recent submits (date range must be attached to a filespec)
        cutoff_str = self._cutoff.strftime("%Y/%m/%d")
        recent_changes, _ = self.conn.run_safe(
            "changes", "-u", username, "-s", "submitted", f"//...@{cutoff_str},@now"
        )
        activity.recent_submits = len(recent_changes) if recent_changes else 0

        # Count pending/shelved
        pending, _ = self.conn.run_safe("changes", "-u", username, "-s", "pending")
        activity.pending_changes = len(pending) if pending else 0

        shelved, _ = self.conn.run_safe("changes", "-u", username, "-s", "shelved")
        activity.shelved_changes = len(shelved) if shelved else 0

        return activity

    def _get_user_groups(self) -> dict[str, list[str]]:
        """Build a map of user -> groups they belong to."""
        user_groups: dict[str, list[str]] = {}

        try:
            groups = self.conn.run("groups")
            for group_data in groups:
                group_name = group_data.get("group", group_data.get("Group", ""))
                if not group_name:
                    continue

                # Get group members
                group_detail, _ = self.conn.run_safe("group", "-o", group_name)
                if group_detail:
                    detail = group_detail[0] if isinstance(group_detail, list) else group_detail
                    # p4python form-parsing returns Users as a Python list
                    members = detail.get("Users", [])
                    if isinstance(members, list):
                        for user in members:
                            user_groups.setdefault(user, []).append(group_name)

        except Exception as e:
            logger.warning("Error getting group membership: %s", e)

        return user_groups
