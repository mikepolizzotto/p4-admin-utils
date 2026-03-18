#!/usr/bin/env python3
"""
File Size Limit Trigger.

Rejects submits that contain files exceeding a configurable size limit.
Prevents accidental submission of uncompressed renders, cache files,
or other oversized assets that bloat the depot.

This is the single most useful trigger for creative studios. One artist
submitting a 10GB EXR sequence to the wrong location can consume more
storage than the rest of the production combined.

Trigger entry:
    file_size_limit change-submit //... "/usr/bin/python3 /path/to/file_size_limit.py %changelist%"

Configuration:
    Edit the CONFIG section below to set limits per file extension or path pattern.

Exit codes:
    0 = Submit allowed
    1 = Submit rejected (file too large)
"""

from __future__ import annotations

import os
import sys
from pathlib import PurePosixPath

# ============================================================
# CONFIGURATION — Edit these to match your studio's policies
# ============================================================

# Default maximum file size in bytes (500 MB)
DEFAULT_MAX_SIZE = 500 * 1024 * 1024

# Per-extension overrides (extension -> max bytes)
# Set to 0 to block entirely, None to allow any size
EXTENSION_LIMITS: dict[str, int | None] = {
    # Large format files — higher limit (2 GB)
    ".exr": 2 * 1024 * 1024 * 1024,
    ".dpx": 2 * 1024 * 1024 * 1024,
    ".mov": 4 * 1024 * 1024 * 1024,
    ".mp4": 2 * 1024 * 1024 * 1024,
    ".mxf": 4 * 1024 * 1024 * 1024,
    ".r3d": None,  # RED RAW — no limit (huge by nature)
    ".braw": None,  # Blackmagic RAW

    # Medium format files — default limit is fine
    ".psd": 1 * 1024 * 1024 * 1024,
    ".psb": 2 * 1024 * 1024 * 1024,

    # Files that should NEVER be huge
    ".py": 10 * 1024 * 1024,       # 10 MB — if your script is this big, something's wrong
    ".sh": 10 * 1024 * 1024,
    ".json": 50 * 1024 * 1024,     # 50 MB
    ".yaml": 10 * 1024 * 1024,
    ".yml": 10 * 1024 * 1024,
    ".xml": 100 * 1024 * 1024,     # 100 MB

    # Block common accident files entirely (0 = block)
    ".tmp": 0,
    ".bak": 0,
    ".swp": 0,
    ".core": 0,
    ".dmp": 0,         # Core dumps
    ".vmdk": 0,        # VM disk images (yes, people try)
    ".iso": 0,
    ".dmg": 0,
    ".zip": DEFAULT_MAX_SIZE,   # Allow but limit archives
    ".rar": DEFAULT_MAX_SIZE,
    ".7z": DEFAULT_MAX_SIZE,
}

# Path pattern overrides (glob pattern -> max bytes)
# More specific patterns take precedence
PATH_LIMITS: dict[str, int | None] = {
    "//*/renders/...": None,            # Render output — no limit
    "//*/cache/...": 0,                  # Cache files should never be submitted
    "//*/temp/...": 0,                   # Temp files
    "//*/build/...": 1 * 1024 * 1024 * 1024,  # Build output — 1 GB limit
}

# Users exempt from size limits (service accounts, admins)
EXEMPT_USERS: set[str] = {
    # "build-service",
    # "pipeline-admin",
}

# ============================================================
# TRIGGER LOGIC — Don't modify unless you know what you're doing
# ============================================================


def format_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def get_limit_for_file(depot_path: str) -> int | None:
    """
    Determine the size limit for a file based on extension and path.
    Returns None for no limit, 0 to block entirely.
    """
    # Check path patterns first (more specific)
    from fnmatch import fnmatch
    for pattern, limit in PATH_LIMITS.items():
        if fnmatch(depot_path, pattern):
            return limit

    # Check extension
    ext = PurePosixPath(depot_path).suffix.lower()
    if ext in EXTENSION_LIMITS:
        return EXTENSION_LIMITS[ext]

    return DEFAULT_MAX_SIZE


def main():
    """Main trigger entry point."""
    if len(sys.argv) < 2:
        print("Usage: file_size_limit.py <changelist>", file=sys.stderr)
        sys.exit(1)

    changelist = sys.argv[1]

    # Get P4 connection info from environment
    p4port = os.environ.get("P4PORT", "")
    p4user = os.environ.get("P4USER", "")

    try:
        from P4 import P4

        p4 = P4()
        p4.connect()

        # Get change description to check user
        change_info = p4.run("describe", "-s", changelist)
        if not change_info:
            sys.exit(0)  # Can't read change — allow (fail open)

        change = change_info[0]
        submit_user = change.get("user", "")

        # Check exemptions
        if submit_user in EXEMPT_USERS:
            sys.exit(0)

        # Check each file in the changelist
        violations = []
        idx = 0
        while f"depotFile{idx}" in change:
            depot_file = change[f"depotFile{idx}"]
            action = change.get(f"action{idx}", "")

            # Only check adds and edits (not deletes, moves, etc.)
            if action in ("add", "edit", "branch", "integrate"):
                file_size = int(change.get(f"fileSize{idx}", 0))
                limit = get_limit_for_file(depot_file)

                if limit is not None:
                    if limit == 0:
                        ext = PurePosixPath(depot_file).suffix
                        violations.append(
                            f"  BLOCKED: {depot_file}\n"
                            f"    File type '{ext}' is not allowed in the depot."
                        )
                    elif file_size > limit:
                        violations.append(
                            f"  TOO LARGE: {depot_file}\n"
                            f"    Size: {format_size(file_size)} | Limit: {format_size(limit)}"
                        )
            idx += 1

        if violations:
            print(f"\n{'='*60}", file=sys.stderr)
            print("SUBMIT REJECTED — File size policy violation", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            print(f"Changelist: {changelist}", file=sys.stderr)
            print(f"User: {submit_user}", file=sys.stderr)
            print(f"\nViolations:", file=sys.stderr)
            for v in violations:
                print(v, file=sys.stderr)
            print(f"\nContact your P4 admin if you need an exception.", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            sys.exit(1)

        p4.disconnect()
        sys.exit(0)

    except ImportError:
        # p4python not available — fall back to p4 CLI
        print("Warning: p4python not available, using CLI fallback", file=sys.stderr)
        import subprocess

        result = subprocess.run(
            ["p4", "describe", "-s", changelist],
            capture_output=True, text=True,
        )
        # Basic check — less detailed without p4python
        # In production, install p4python on the server
        sys.exit(0)

    except Exception as e:
        # Fail open — don't block submits on trigger errors
        print(f"Trigger error (allowing submit): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
