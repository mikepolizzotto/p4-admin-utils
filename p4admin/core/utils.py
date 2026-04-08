"""
Shared utility functions for p4admin tools.

Centralizes common operations like date parsing and size formatting
that are used across multiple modules.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def parse_p4_date(date_str: str | None) -> datetime | None:
    """
    Parse a P4 date string into a datetime object.

    Handles both epoch timestamps (from tagged output like p4 clients,
    p4 changes) and formatted date strings (from some spec fields).

    Args:
        date_str: Date string from P4 output, or None.

    Returns:
        Parsed datetime, or None if unparseable.
    """
    if not date_str:
        return None
    # P4 tagged output typically returns epoch timestamps as strings
    try:
        return datetime.fromtimestamp(int(date_str))
    except (ValueError, TypeError, OSError):
        pass
    # Some P4 commands return formatted dates
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, TypeError):
            continue
    logger.warning("Could not parse P4 date: %s", date_str)
    return None


def format_bytes(size_bytes: int) -> str:
    """
    Convert bytes to a human-readable string.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Formatted string like "1.5 GB".
    """
    if size_bytes == 0:
        return "0 B"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
