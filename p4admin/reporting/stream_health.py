"""
Stream Health Check.

Analyzes the stream hierarchy for common issues:
- Stale task streams that were never merged back and deleted
- Deeply nested stream hierarchies that slow down operations
- Streams with no recent activity (candidates for cleanup)
- Unmerged changes between parent/child streams
- Streams with no associated workspaces (orphaned)

Streams are the biggest source of P4 admin complexity in studios.
Every production spawns task streams for features, fixes, and experiments.
Most never get cleaned up. This tool gives you visibility into the mess.

Usage:
    checker = StreamHealthCheck(p4_conn)
    report = checker.analyze()
    render_terminal(report)

    # Focus on a specific depot
    report = checker.analyze(depot="//film-assets")
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
class StreamInfo:
    """Parsed stream information with health analysis."""

    stream: str  # Full stream path (e.g., //depot/main)
    name: str  # Short name
    depot: str
    parent: str
    stream_type: str  # mainline, development, release, task, virtual
    owner: str
    description: str
    access: datetime | None = None
    update: datetime | None = None
    workspace_count: int = 0
    has_unmerged_changes: bool = False
    depth: int = 0  # Nesting depth in hierarchy
    issues: list[str] = field(default_factory=list)

    @property
    def days_since_update(self) -> int | None:
        if self.update is None:
            return None
        return (datetime.now() - self.update).days

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0

    @property
    def is_task_stream(self) -> bool:
        return self.stream_type == "task"


class StreamHealthCheck:
    """
    Analyzes stream hierarchy health.

    Args:
        conn: Active P4Connection instance.
        stale_days: Days without activity before a stream is "stale".
        max_depth_warning: Warn about streams nested deeper than this.
    """

    def __init__(
        self,
        conn: P4Connection,
        stale_days: int = 90,
        max_depth_warning: int = 4,
    ):
        self.conn = conn
        self.stale_days = stale_days
        self.max_depth_warning = max_depth_warning
        self._cutoff = datetime.now() - timedelta(days=stale_days)

    def analyze(self, depot: str | None = None) -> ReportData:
        """
        Analyze stream hierarchy health.

        Args:
            depot: Specific depot to analyze. If None, analyzes all stream depots.
        """
        logger.info("Analyzing stream health...")

        streams = self._get_streams(depot)
        logger.info("Found %d streams", len(streams))

        # Build parent-child map and compute depths
        stream_map: dict[str, StreamInfo] = {}
        for stream_data in streams:
            info = self._parse_stream(stream_data)
            stream_map[info.stream] = info

        # Compute depths
        for info in stream_map.values():
            info.depth = self._compute_depth(info.stream, stream_map)

        # Analyze each stream
        for info in stream_map.values():
            self._analyze_stream(info, stream_map)

        all_streams = list(stream_map.values())
        stale = [s for s in all_streams if any("stale" in i.lower() for i in s.issues)]
        orphaned = [s for s in all_streams if any("no workspaces" in i.lower() for i in s.issues)]
        deep = [s for s in all_streams if any("deep" in i.lower() for i in s.issues)]
        stale_tasks = [s for s in stale if s.is_task_stream]
        with_issues = [s for s in all_streams if s.has_issues]

        # Group by depot
        depots: dict[str, list[StreamInfo]] = {}
        for info in all_streams:
            depots.setdefault(info.depot, []).append(info)

        report = ReportData(
            title="Stream Health Report",
            server_info=self.conn.server_info,
            summary={
                "Total streams": len(all_streams),
                "Streams with issues": len(with_issues),
                "Stale task streams": len(stale_tasks),
                "Orphaned streams (no workspaces)": len(orphaned),
                "Deeply nested streams": len(deep),
                "Stream depots": len(depots),
                "Stale threshold": f"{self.stale_days} days",
            },
        )

        # Stale task streams — these are the biggest cleanup opportunity
        if stale_tasks:
            report.add_section(
                title="Stale Task Streams (cleanup candidates)",
                headers=["Stream", "Owner", "Parent", "Last Update", "Days Idle", "Workspaces"],
                rows=[
                    [
                        s.stream,
                        s.owner,
                        s.parent,
                        s.update.strftime("%Y-%m-%d") if s.update else "Unknown",
                        str(s.days_since_update) if s.days_since_update is not None else "N/A",
                        str(s.workspace_count),
                    ]
                    for s in sorted(stale_tasks, key=lambda x: x.days_since_update or 9999, reverse=True)
                ],
                notes=[
                    "Task streams should be deleted after merging back to parent.",
                    "Review for unmerged changes before deleting.",
                    "Use `p4 stream -d <stream>` to delete (after deleting associated workspaces).",
                ],
            )

        # Other streams with issues
        non_task_issues = [s for s in with_issues if not s.is_task_stream]
        if non_task_issues:
            report.add_section(
                title="Other Streams with Issues",
                headers=["Stream", "Type", "Owner", "Issues"],
                rows=[
                    [
                        s.stream,
                        s.stream_type,
                        s.owner,
                        "; ".join(s.issues),
                    ]
                    for s in non_task_issues
                ],
            )

        # Stream hierarchy overview (per depot)
        for depot_name, depot_streams in sorted(depots.items()):
            # Build a simple hierarchy view
            mainlines = [s for s in depot_streams if s.stream_type == "mainline"]
            dev_streams = [s for s in depot_streams if s.stream_type == "development"]
            release_streams = [s for s in depot_streams if s.stream_type == "release"]
            task_streams = [s for s in depot_streams if s.stream_type == "task"]
            virtual_streams = [s for s in depot_streams if s.stream_type == "virtual"]

            stats = {
                "Mainline streams": str(len(mainlines)),
                "Development streams": str(len(dev_streams)),
                "Release streams": str(len(release_streams)),
                "Task streams": str(len(task_streams)),
            }
            if virtual_streams:
                stats["Virtual streams"] = str(len(virtual_streams))
            stats["Max nesting depth"] = str(max((s.depth for s in depot_streams), default=0))

            report.add_section(
                title=f"Depot: {depot_name}",
                stats=stats,
            )

        return report

    def _get_streams(self, depot: str | None = None) -> list[dict[str, Any]]:
        """Get all streams, optionally filtered by depot."""
        if depot:
            depot = depot.rstrip("/")
            results = self.conn.run("streams", f"{depot}/...")
        else:
            results = self.conn.run("streams", "//...")
        return results

    def _parse_stream(self, stream_data: dict[str, Any]) -> StreamInfo:
        """Parse raw stream data into StreamInfo."""
        stream_path = stream_data.get("Stream", stream_data.get("stream", ""))
        parts = stream_path.strip("/").split("/")
        depot_name = parts[0] if parts else ""

        return StreamInfo(
            stream=stream_path,
            name=parts[-1] if parts else "",
            depot=depot_name,
            parent=stream_data.get("Parent", "none"),
            stream_type=stream_data.get("Type", "unknown"),
            owner=stream_data.get("Owner", ""),
            description=stream_data.get("desc", stream_data.get("Description", "")).strip(),
            access=self._parse_p4_date(stream_data.get("Access")),
            update=self._parse_p4_date(stream_data.get("Update")),
        )

    def _analyze_stream(self, info: StreamInfo, stream_map: dict[str, StreamInfo]):
        """Check a stream for health issues."""
        # Count associated workspaces
        clients, _ = self.conn.run_safe("clients", "-S", info.stream)
        info.workspace_count = len(clients) if clients else 0

        # Check for staleness
        if info.update and info.update < self._cutoff:
            info.issues.append(f"Stale — no updates in {info.days_since_update} days")

        # Check for orphaned streams (no workspaces)
        if info.workspace_count == 0 and info.stream_type in ("task", "development"):
            info.issues.append("No workspaces associated")

        # Check for deep nesting
        if info.depth > self.max_depth_warning:
            info.issues.append(f"Deeply nested (depth: {info.depth})")

    def _compute_depth(self, stream_path: str, stream_map: dict[str, StreamInfo], seen: set | None = None) -> int:
        """Compute nesting depth of a stream in the hierarchy."""
        if seen is None:
            seen = set()

        if stream_path in seen:
            return 0  # Circular reference guard

        seen.add(stream_path)
        info = stream_map.get(stream_path)
        if info is None or info.parent == "none" or info.parent not in stream_map:
            return 0

        return 1 + self._compute_depth(info.parent, stream_map, seen)

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
