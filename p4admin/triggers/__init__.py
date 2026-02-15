"""
Server-side trigger scripts for Perforce guardrails.

Triggers are standalone scripts deployed to the P4 server that run on
specific events (submit, change, shelve, etc.). They enforce rules that
prevent common problems before they happen.

These scripts are designed to be copied to your P4 server and referenced
in the triggers table. They're self-contained — no external dependencies
beyond Python 3.10+ and the P4 environment.

Installation:
    1. Copy trigger scripts to your P4 server (e.g., /opt/perforce/triggers/)
    2. Add trigger entries via `p4 triggers`:

        file_size_limit change-submit //... "/usr/bin/python3 /opt/perforce/triggers/file_size_limit.py %changelist%"
        binary_check change-submit //... "/usr/bin/python3 /opt/perforce/triggers/binary_type_check.py %changelist%"
        naming_check change-submit //... "/usr/bin/python3 /opt/perforce/triggers/naming_convention.py %changelist%"
"""
