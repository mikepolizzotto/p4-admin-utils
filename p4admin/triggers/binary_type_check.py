#!/usr/bin/env python3
"""
Binary File Type Enforcement Trigger.

Catches common typemap mistakes at submit time:
- Binary files added as text (corrupts the file)
- Text files added as binary (wastes space, loses diff/merge)
- Files that should use +S (lazy copy) but don't

The Perforce typemap *should* handle this, but it only applies at add time.
If someone's workspace typemap is misconfigured or they use `p4 add -t text`
on a binary file, the typemap won't save them. This trigger is the safety net.

Trigger entry:
    binary_check change-submit //... "/usr/bin/python3 /path/to/binary_type_check.py %changelist%"

Exit codes:
    0 = Submit allowed
    1 = Submit rejected (type mismatch detected)
"""

from __future__ import annotations

import os
import sys
from pathlib import PurePosixPath

# ============================================================
# CONFIGURATION
# ============================================================

# Extensions that MUST be stored as binary
MUST_BE_BINARY: set[str] = {
    # Images
    ".exr", ".dpx", ".hdr", ".tif", ".tiff", ".psd", ".psb",
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".gif", ".dds",
    # 3D/Scene
    ".mb", ".ma", ".hip", ".hipnc", ".hda", ".blend", ".c4d",
    ".max", ".ztl", ".zpr", ".fbx", ".abc", ".obj", ".usd",
    ".usdc", ".usdz", ".vdb",
    # Game engine
    ".uasset", ".umap", ".unity", ".prefab", ".asset",
    # Video/Audio
    ".mov", ".mp4", ".avi", ".mxf", ".r3d", ".braw",
    ".wav", ".aif", ".aiff", ".mp3", ".ogg",
    # Documents
    ".pdf", ".docx", ".xlsx", ".pptx",
    # Archives
    ".zip", ".rar", ".7z", ".tar", ".gz",
    # Compiled
    ".dll", ".so", ".dylib", ".exe", ".pdb",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2",
}

# Extensions that MUST be stored as text
MUST_BE_TEXT: set[str] = {
    ".py", ".sh", ".bash", ".bat", ".cmd", ".ps1",
    ".js", ".ts", ".jsx", ".tsx",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".java",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
    ".xml", ".html", ".htm", ".css", ".scss",
    ".md", ".txt", ".rst", ".csv",
    ".mel", ".vex", ".hlsl", ".glsl",
    ".edl", ".srt", ".vtt", ".otio",
    ".usda",  # ASCII USD
    ".meta",  # Unity meta files
}

# Extensions that should use +S (lazy copy) — important for streams
SHOULD_USE_LAZY_COPY: set[str] = {
    ".exr", ".dpx", ".hdr", ".psd", ".psb",
    ".mb", ".hip", ".blend", ".fbx", ".abc", ".usd", ".usdc", ".vdb",
    ".uasset", ".umap",
    ".mov", ".mp4", ".mxf",
    ".wav", ".aif",
    ".r3d", ".braw",
}

# How strict should we be?
# "reject" = block the submit
# "warn" = allow but print a warning
MODE = "warn"

# Users exempt from this check
EXEMPT_USERS: set[str] = set()

# ============================================================
# TRIGGER LOGIC
# ============================================================


def check_file_type(depot_path: str, p4_type: str) -> str | None:
    """
    Check if a file's P4 type matches what's expected for its extension.

    Returns an error message string if there's a mismatch, None if OK.
    """
    ext = PurePosixPath(depot_path).suffix.lower()
    base_type = p4_type.split("+")[0] if "+" in p4_type else p4_type
    modifiers = p4_type.split("+")[1] if "+" in p4_type else ""

    issues = []

    # Check binary/text mismatch
    if ext in MUST_BE_BINARY and base_type == "text":
        issues.append(
            f"  TYPE MISMATCH: {depot_path}\n"
            f"    Extension '{ext}' should be binary, but stored as '{p4_type}'.\n"
            f"    This will corrupt the file. Fix with: p4 retype -t binary+S {depot_path}"
        )

    elif ext in MUST_BE_TEXT and base_type in ("binary", "ubinary"):
        issues.append(
            f"  TYPE MISMATCH: {depot_path}\n"
            f"    Extension '{ext}' should be text, but stored as '{p4_type}'.\n"
            f"    This wastes space and disables diff/merge. Fix with: p4 retype -t text {depot_path}"
        )

    # Check lazy copy (+S)
    if ext in SHOULD_USE_LAZY_COPY and "S" not in modifiers and base_type != "text":
        issues.append(
            f"  MISSING +S: {depot_path}\n"
            f"    Large binary '{ext}' should use lazy copy (+S) for stream efficiency.\n"
            f"    Current type: '{p4_type}'. Recommended: 'binary+S'"
        )

    return "\n".join(issues) if issues else None


def main():
    """Main trigger entry point."""
    if len(sys.argv) < 2:
        print("Usage: binary_type_check.py <changelist>", file=sys.stderr)
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

        violations = []
        warnings = []
        idx = 0

        while f"depotFile{idx}" in change:
            depot_file = change[f"depotFile{idx}"]
            file_type = change.get(f"type{idx}", "")
            action = change.get(f"action{idx}", "")

            # Only check adds and edits
            if action in ("add", "edit", "branch"):
                issue = check_file_type(depot_file, file_type)
                if issue:
                    if "MISMATCH" in issue:
                        violations.append(issue)
                    else:
                        warnings.append(issue)
            idx += 1

        # Print warnings regardless
        if warnings:
            print(f"\n{'='*60}", file=sys.stderr)
            print("FILE TYPE WARNINGS", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            for w in warnings:
                print(w, file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)

        # Handle violations based on mode
        if violations:
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"{'SUBMIT REJECTED' if MODE == 'reject' else 'FILE TYPE WARNINGS'} — Type mismatch detected", file=sys.stderr)
            print(f"{'='*60}", file=sys.stderr)
            print(f"Changelist: {changelist}", file=sys.stderr)
            print(f"User: {submit_user}", file=sys.stderr)
            print(f"\nIssues:", file=sys.stderr)
            for v in violations:
                print(v, file=sys.stderr)
            print(f"\nFix the file types and resubmit.", file=sys.stderr)
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
