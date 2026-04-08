"""
Depot Size Analyzer.

Answers the questions every P4 admin gets asked:
- "Why is the server using so much disk?"
- "Which depot is the biggest?"
- "Who's submitting the most data?"
- "Which paths are growing the fastest?"

P4Admin shows you individual files but has no aggregate view.
`p4 sizes` works but requires you to know which paths to check.
This tool scans all depots and gives you a top-down storage breakdown.

Usage:
    analyzer = DepotSizeAnalyzer(p4_conn)
    report = analyzer.analyze()
    render_terminal(report)

    # Focus on a specific depot
    report = analyzer.analyze(depot="//assets")

    # Show top N largest paths
    report = analyzer.analyze(top_paths=20)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from p4admin.core.connection import P4Connection
from p4admin.core.output import ReportData
from p4admin.core.utils import format_bytes

logger = logging.getLogger(__name__)


@dataclass
class DepotInfo:
    """Size and file count information for a depot."""

    name: str
    depot_type: str  # local, remote, stream, etc.
    file_count: int = 0
    total_size: int = 0  # bytes
    head_file_count: int = 0  # files at head revision only
    head_size: int = 0  # size at head revision only
    revision_count: int = 0  # total revisions across all files

    @property
    def size_display(self) -> str:
        return format_bytes(self.total_size)

    @property
    def head_size_display(self) -> str:
        return format_bytes(self.head_size)

    @property
    def avg_revisions(self) -> float:
        if self.head_file_count == 0:
            return 0
        return self.revision_count / self.head_file_count


@dataclass
class PathSizeInfo:
    """Size info for a specific depot path."""

    path: str
    file_count: int = 0
    total_size: int = 0
    depth: int = 0  # How deep in the path hierarchy

    @property
    def size_display(self) -> str:
        return format_bytes(self.total_size)


@dataclass
class UserStorageInfo:
    """Storage consumed by a specific user."""

    user: str
    file_count: int = 0
    total_size: int = 0

    @property
    def size_display(self) -> str:
        return format_bytes(self.total_size)


class DepotSizeAnalyzer:
    """
    Analyzes storage usage across Perforce depots.

    Args:
        conn: Active P4Connection instance.
        top_paths: Number of largest paths to include in report.
        top_users: Number of largest storage consumers to include.
        path_depth: How deep to analyze path breakdowns (1 = top-level dirs only).
    """

    def __init__(
        self,
        conn: P4Connection,
        top_paths: int = 15,
        top_users: int = 10,
        path_depth: int = 2,
    ):
        self.conn = conn
        self.top_paths = top_paths
        self.top_users = top_users
        self.path_depth = path_depth

    def analyze(self, depot: str | None = None) -> ReportData:
        """
        Analyze depot storage usage.

        Args:
            depot: Specific depot to analyze (e.g., "//assets"). If None, analyzes all.

        Returns:
            ReportData with storage breakdown.
        """
        logger.info("Analyzing depot storage...")

        if depot:
            depots_to_scan = [{"name": depot.strip("/"), "type": "local"}]
        else:
            depots_to_scan = self._get_depots()

        depot_infos: list[DepotInfo] = []
        all_path_sizes: list[PathSizeInfo] = []
        total_size = 0
        total_files = 0

        for depot_data in depots_to_scan:
            depot_name = depot_data.get("name", depot_data.get("Depot", ""))
            depot_type = depot_data.get("type", depot_data.get("Type", "local"))

            # Skip remote/spec depots — we can't size them
            if depot_type in ("remote", "spec", "unload", "archive"):
                logger.debug("Skipping %s depot: %s", depot_type, depot_name)
                continue

            info = self._analyze_depot(depot_name, depot_type)
            depot_infos.append(info)
            total_size += info.total_size
            total_files += info.head_file_count

            # Get path breakdown for this depot
            path_sizes = self._analyze_depot_paths(depot_name)
            all_path_sizes.extend(path_sizes)

        # Sort depots by size
        depot_infos.sort(key=lambda d: d.total_size, reverse=True)

        # Sort paths by size, take top N
        all_path_sizes.sort(key=lambda p: p.total_size, reverse=True)
        top_paths = all_path_sizes[: self.top_paths]

        # Build report
        report = ReportData(
            title="Depot Storage Analysis",
            server_info=self.conn.server_info,
            summary={
                "Total storage (all revisions)": format_bytes(total_size),
                "Total files at head": f"{total_files:,}",
                "Depots analyzed": len(depot_infos),
            },
        )

        # Per-depot breakdown
        if depot_infos:
            report.add_section(
                title="Storage by Depot",
                headers=["Depot", "Type", "Head Files", "Head Size", "All Revisions", "Avg Rev/File"],
                rows=[
                    [
                        f"//{d.name}",
                        d.depot_type,
                        f"{d.head_file_count:,}",
                        d.head_size_display,
                        d.size_display,
                        f"{d.avg_revisions:.1f}",
                    ]
                    for d in depot_infos
                ],
            )

        # Top paths by size
        if top_paths:
            report.add_section(
                title=f"Largest Paths (Top {self.top_paths})",
                headers=["Path", "Files", "Size"],
                rows=[
                    [p.path, f"{p.file_count:,}", p.size_display]
                    for p in top_paths
                ],
                notes=[
                    "Size includes all revisions, not just head.",
                    "Use `p4 sizes -s //depot/path/...` for real-time checks.",
                ],
            )

        return report

    def _get_depots(self) -> list[dict[str, Any]]:
        """Get all depots on the server."""
        results = self.conn.run("depots")
        logger.info("Found %d depots", len(results))
        return results

    def _analyze_depot(self, depot_name: str, depot_type: str) -> DepotInfo:
        """Get size information for a single depot."""
        info = DepotInfo(name=depot_name, depot_type=depot_type)

        try:
            # Get sizes for all revisions
            sizes, errors = self.conn.run_safe("sizes", "-a", "-s", f"//{depot_name}/...")
            if sizes and isinstance(sizes, list):
                for entry in sizes:
                    if isinstance(entry, dict):
                        info.total_size += int(entry.get("fileSize", 0))
                        info.file_count += int(entry.get("fileCount", 0))

            # Get sizes for head revision only
            head_sizes, _ = self.conn.run_safe("sizes", "-s", f"//{depot_name}/...")
            if head_sizes and isinstance(head_sizes, list):
                for entry in head_sizes:
                    if isinstance(entry, dict):
                        info.head_size += int(entry.get("fileSize", 0))
                        info.head_file_count += int(entry.get("fileCount", 0))

            # Estimate revision count
            if info.file_count > 0 and info.head_file_count > 0:
                info.revision_count = info.file_count

        except Exception as e:
            logger.warning("Error analyzing depot %s: %s", depot_name, e)

        return info

    def _analyze_depot_paths(self, depot_name: str) -> list[PathSizeInfo]:
        """Get size breakdown for top-level paths within a depot."""
        paths: list[PathSizeInfo] = []

        try:
            # List top-level directories
            dirs, _ = self.conn.run_safe("dirs", f"//{depot_name}/*")
            if not dirs:
                return paths

            for dir_entry in dirs:
                dir_path = dir_entry if isinstance(dir_entry, str) else dir_entry.get("dir", "")
                if not dir_path:
                    continue

                size_info = PathSizeInfo(path=dir_path, depth=1)

                sizes, _ = self.conn.run_safe("sizes", "-a", "-s", f"{dir_path}/...")
                if sizes and isinstance(sizes, list):
                    for entry in sizes:
                        if isinstance(entry, dict):
                            size_info.total_size += int(entry.get("fileSize", 0))
                            size_info.file_count += int(entry.get("fileCount", 0))

                if size_info.total_size > 0:
                    paths.append(size_info)

        except Exception as e:
            logger.warning("Error analyzing paths in %s: %s", depot_name, e)

        return paths
