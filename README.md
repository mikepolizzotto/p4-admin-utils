# p4-admin-utils

Perforce administration utilities for creative studio environments — VFX, animation, games, and post-production.

The stuff P4Admin can't do, and the stuff it can do but shouldn't require 45 clicks.

## Why This Exists

Every studio running Perforce solves the same admin problems: dead workspaces from departed freelancers, license seats consumed by inactive users, manual production setup that takes a week, and trigger scripts copy-pasted from Stack Overflow. This repo is a collection of those solutions, generalized and documented so you're not reinventing them on every show.

## Tools

### Cleanup

**Stale Workspace Finder** — Identifies abandoned workspaces where the owner hasn't synced in N days, the owner no longer exists, or there's been no activity since the last production. Checks for pending/shelved changes before recommending deletion.

```bash
p4admin workspaces                          # Report stale workspaces (90-day default)
p4admin workspaces --stale-days 60          # Custom threshold
p4admin workspaces --exclude 'build-*'      # Skip CI workspaces
p4admin workspaces --cleanup --dry-run      # Preview cleanup
p4admin workspaces --cleanup                # Execute (safe workspaces only)
```

**Orphaned Shelf Cleaner** — Finds shelved changelists from users who no longer exist or haven't been touched in months. These are hidden storage hogs.

```bash
p4admin shelves                             # Report orphaned shelves
p4admin shelves --stale-days 30             # 30-day threshold
p4admin shelves --cleanup --owner-gone-only # Only clean up departed users
```

**Empty Changelist Pruner** — Removes pending changelists with no open or shelved files. Accumulated from reverted changes, failed submits, and automated tools that don't clean up.

```bash
p4admin changelists                         # Report empty changelists
p4admin changelists --min-age 30            # 30+ days old only
p4admin changelists --cleanup --dry-run     # Preview cleanup
```

### Reporting

**Server Health & License Dashboard** — Quick snapshot of license status, seat utilization, inactive users, version info, and support contacts.

```bash
p4admin health                              # Terminal dashboard
p4admin health --inactive-days 60           # Custom inactivity threshold
p4admin health --format json                # JSON (for automation)
p4admin health --output status.html         # HTML (for management)
```

**Depot Size Analyzer** — Per-depot storage breakdown, largest paths, and growth analysis. Answers "why is the server using so much disk?"

```bash
p4admin depot-size                          # All depots
p4admin depot-size --depot //assets         # Specific depot
p4admin depot-size --top-paths 25           # More detail
p4admin depot-size --output storage.html    # HTML report
```

**User Activity Report** — Who's active, who's inactive, who's never connected. Useful for license optimization and catching onboarding issues.

```bash
p4admin users                               # 90-day activity window
p4admin users --days 30                     # Last 30 days
p4admin users --output users.html           # HTML report
```

**Stream Health Check** — Finds stale task streams, orphaned streams with no workspaces, and deeply nested hierarchies that need cleanup.

```bash
p4admin streams                             # All stream depots
p4admin streams --depot //film-assets       # Specific depot
p4admin streams --stale-days 60             # Custom threshold
```

### Production Setup

**Production Scaffolding** — Creates depot, streams, groups, and protections for a new show/game from a YAML config. Turns a week of P4Admin clicking into one command.

```bash
p4admin scaffold configs/new-film.yaml              # Preview
p4admin scaffold configs/new-film.yaml --apply       # Create everything
```

**Typemap Templates** — Curated typemap configurations for VFX, games, and post-production pipelines. Handles the extensions P4's defaults get wrong (EXR as text, anyone?).

```bash
p4admin typemap --list                      # List available templates
p4admin typemap vfx                         # Preview VFX typemap
p4admin typemap games --apply               # Apply games typemap
```

**Permission Group Templates** — Role-based group hierarchies mapped to production roles (artists, leads, TDs, producers, admins). Templates for film, games, and post.

```bash
p4admin permissions --list                  # List templates
p4admin permissions film titan              # Preview for production "titan"
p4admin permissions games starfield --apply # Create groups
```

### Server-Side Triggers

Standalone scripts deployed to the P4 server to enforce rules at submit time. Copy to your server, add to the triggers table, done.

**File Size Limit** — Rejects submits with files exceeding configurable limits. Per-extension and per-path overrides. Blocks common accident files (.tmp, .iso, .vmdk). The single most useful trigger for creative studios.

**Binary File Type Check** — Catches binary files stored as text (corrupts them) and text files stored as binary (wastes space). Warns about missing `+S` (lazy copy) on large binaries.

**Naming Convention Enforcement** — Validates file paths against studio standards. Catches spaces in filenames, uppercase extensions, forbidden characters, and manual versioning in filenames (`_v2`, `_final`, `(copy)`).

## Installation

```bash
# Clone the repo
git clone https://github.com/mikepolizzotto/p4-admin-utils.git
cd p4-admin-utils

# Install (requires Python 3.10+)
pip install -e .

# Or with dev dependencies
pip install -e ".[dev]"
```

### Prerequisites

- **Python 3.10+**
- **p4python** — Perforce's Python API ([installation guide](https://www.perforce.com/manuals/p4python/Content/P4Python/python.installation.html))
- A Perforce server with admin-level access for most tools
- P4 environment variables set (`P4PORT`, `P4USER`) or a config file

## Configuration

Copy the example config and customize for your environment:

```bash
cp configs/examples/server.example.yaml server.yaml
```

All configuration is optional. If you have `P4PORT` and `P4USER` set in your environment, the tools will work without a config file. The config adds things like support contact info, custom thresholds, and workspace exclusion patterns.

See [configs/examples/server.example.yaml](configs/examples/server.example.yaml) for all available options.

## Output Formats

All tools support multiple output formats:

| Format     | Flag                  | Use Case                              |
|------------|----------------------|---------------------------------------|
| Terminal   | `--format terminal`  | Interactive use (default)             |
| JSON       | `--format json`      | Automation, piping, monitoring tools  |
| Markdown   | `--output report.md` | Documentation, wiki, pull requests    |
| HTML       | `--output report.html`| Sharing with non-technical stakeholders|

## Project Structure

```
p4-admin-utils/
├── p4admin/
│   ├── core/              # P4 connection, config, output formatting
│   ├── cleanup/           # Workspace, shelf, changelist cleanup
│   │   ├── stale_workspaces.py
│   │   ├── orphaned_shelves.py
│   │   └── empty_changelists.py
│   ├── reporting/         # Depot size, user activity, stream health
│   │   ├── depot_size.py
│   │   ├── user_activity.py
│   │   └── stream_health.py
│   ├── licensing/         # License info, seat analysis, server health
│   │   └── health_check.py
│   ├── setup/             # Production scaffolding, typemaps, permissions
│   │   ├── scaffolding.py
│   │   ├── typemaps.py
│   │   └── permissions.py
│   ├── triggers/          # Server-side trigger scripts
│   │   ├── file_size_limit.py
│   │   ├── binary_type_check.py
│   │   └── naming_convention.py
│   └── cli/               # CLI entry points
├── configs/
│   └── examples/          # Sample YAML configs
├── docs/
└── tests/
```

## Built For

Studios, production companies, and creative teams running Perforce for:
- VFX & Animation
- Game Development
- Post-Production & Editorial
- Virtual Production
- Any creative pipeline with versioned assets

## Contributing

Issues and PRs welcome. If you're a P4 admin at a studio and have a utility you'd like to see generalized and included, open an issue describing the problem it solves.

## License

MIT — see [LICENSE](LICENSE) for details.
