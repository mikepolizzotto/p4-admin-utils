#!/usr/bin/env python3
"""
Naming Convention Enforcement Trigger.

Validates that submitted file paths conform to studio naming standards.
Catches common problems:
- Spaces in file paths (breaks scripts and some tools)
- Uppercase extensions (.EXR instead of .exr)
- Special characters that cause cross-platform issues
- Files outside of allowed directory structures
- Versioning in filenames when P4 should handle versioning

Every studio has naming conventions, but nobody enforces them until
someone submits "Final_Final_v3_REAL (copy).psd" and breaks the pipeline.

Trigger entry:
    naming_check change-submit //... "/usr/bin/python3 /path/to/naming_convention.py %changelist%"

Exit codes:
    0 = Submit allowed
    1 = Submit rejected (naming violation)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import PurePosixPath

# ============================================================
# CONFIGURATION
# ============================================================

# Reject files with spaces in their names
# This is the #1 cause of broken pipeline scripts
REJECT_SPACES = True

# Require lowercase file extensions
# Prevents .EXR / .exr duplication issues
REQUIRE_LOWERCASE_EXTENSIONS = True

# Characters not allowed in file names (beyond what P4 already blocks)
FORBIDDEN_CHARACTERS = set("!@#$%^&()+={}[]|;',`~")

# Patterns that suggest bad versioning habits
# (versioning should be handled by P4, not filenames)
BAD_VERSION_PATTERNS: list[re.Pattern] = [
    re.compile(r"_v\d+\.", re.IGNORECASE),           # file_v2.ext
    re.compile(r"_final", re.IGNORECASE),             # file_final.ext
    re.compile(r"_latest", re.IGNORECASE),            # file_latest.ext
    re.compile(r"\s*\(copy\)", re.IGNORECASE),        # file (copy).ext
    re.compile(r"\s*copy\s*\d*\.", re.IGNORECASE),    # file copy 2.ext
    re.compile(r"_old\.", re.IGNORECASE),             # file_old.ext
    re.compile(r"_backup\.", re.IGNORECASE),          # file_backup.ext
    re.compile(r"_bak\.", re.IGNORECASE),             # file_bak.ext
]

# Maximum filename length (not including path)
MAX_FILENAME_LENGTH = 200

# Maximum total path depth (number of directories)
MAX_PATH_DEPTH = 15

# Paths exempt from naming checks (regex patterns)
EXEMPT_PATHS: list[re.Pattern] = [
    re.compile(r"//[^/]+/third_party/"),     # Third-party code
    re.compile(r"//[^/]+/vendor/"),           # Vendor libraries
    re.compile(r"//[^/]+/external/"),         # External dependencies
]

# How strict: "reject" or "warn"
MODE = "warn"

# Users exempt from naming checks
EXEMPT_USERS: set[str] = set()

# ============================================================
# TRIGGER LOGIC
# ============================================================


def check_naming(depot_path: str) -> list[str]:
    """
    Check a depot path for naming convention violations.

    Returns list of violation messages (empty if all OK).
    """
    # Check exemptions
    for pattern in EXEMPT_PATHS:
        if pattern.search(depot_path):
            return []

    violations = []
    path = PurePosixPath(depot_path)
    filename = path.name
    extension = path.suffix

    # Check spaces
    if REJECT_SPACES and " " in filename:
        violations.append(
            f"  SPACES: {depot_path}\n"
            f"    Filename contains spaces. Use underscores or hyphens instead.\n"
            f"    Rename to: {filename.replace(' ', '_')}"
        )

    # Check extension case
    if REQUIRE_LOWERCASE_EXTENSIONS and extension and extension != extension.lower():
        violations.append(
            f"  UPPERCASE EXT: {depot_path}\n"
            f"    Extension should be lowercase: '{extension}' → '{extension.lower()}'"
        )

    # Check forbidden characters
    bad_chars = set(filename) & FORBIDDEN_CHARACTERS
    if bad_chars:
        violations.append(
            f"  BAD CHARS: {depot_path}\n"
            f"    Filename contains forbidden characters: {', '.join(repr(c) for c in bad_chars)}"
        )

    # Check bad versioning patterns
    for pattern in BAD_VERSION_PATTERNS:
        if pattern.search(filename):
            violations.append(
                f"  VERSION IN NAME: {depot_path}\n"
                f"    Filename appears to contain manual versioning (matched: {pattern.pattern}).\n"
                f"    Let Perforce handle versioning — submit to the same filename."
            )
            break  # One version warning is enough

    # Check filename length
    if len(filename) > MAX_FILENAME_LENGTH:
        violations.append(
            f"  NAME TOO LONG: {depot_path}\n"
            f"    Filename is {len(filename)} characters (max: {MAX_FILENAME_LENGTH})."
        )

    # Check path depth
    parts = depot_path.strip("/").split("/")
    if len(parts) > MAX_PATH_DEPTH:
        violations.append(
            f"  PATH TOO DEEP: {depot_path}\n"
            f"    Path is {len(parts)} levels deep (max: {MAX_PATH_DEPTH})."
        )

    return violations


def main():
    """Main trigger entry point."""
    if len(sys.argv) < 2:
        print("Usage: naming_convention.py <changelist>", file=sys.stderr)
        sys.exit(1)

    changelist = sys.argv[1]

    try:
        from P4 import P4

        p4 = P4()
        p4.connect()

        change_info = p4.run("describe", "-s", changelist)
        if not change_info:
            sys.exit(0)

        change = change_info[0]
        submit_user = change.get("user", "")

        if submit_user in EXEMPT_USERS:
            sys.exit(0)

        all_violations = []
        idx = 0

        while f"depotFile{idx}" in change:
            depot_file = change[f"depotFile{idx}"]
            action = change.get(f"action{idx}", "")

            # Only check adds and branches (not edits to existing files)
            if action in ("add", "branch", "move/add"):
                violations = check_naming(depot_file)
                all_violations.extend(violations)
            idx += 1

        if all_violations:
            print(f"\n{'='*60}", file=sys.stderr)
            title = "SUBMIT REJECTED" if MODE == "reject" else "NAMING CONVENTION WARNINGS"
            print(f"{title}", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            print(f"Changelist: {changelist}", file=sys.stderr)
            print(f"User: {submit_user}", file=sys.stderr)
            print(f"\nIssues ({len(all_violations)}):", file=sys.stderr)
            for v in all_violations:
                print(v, file=sys.stderr)
            print(f"\nPlease fix naming and resubmit.", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)

            if MODE == "reject":
                sys.exit(1)

        p4.disconnect()
        sys.exit(0)

    except ImportError:
        print("Warning: p4python not available", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Trigger error (allowing submit): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
