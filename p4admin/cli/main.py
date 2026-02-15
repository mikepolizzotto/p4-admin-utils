"""
p4admin CLI — Perforce administration utilities.

Usage:
    p4admin workspaces [--stale-days 90] [--cleanup] [--dry-run]
    p4admin shelves [--stale-days 60] [--cleanup] [--dry-run]
    p4admin changelists [--min-age 7] [--cleanup] [--dry-run]
    p4admin health [--inactive-days 90]
    p4admin depot-size [--depot //name] [--top-paths 15]
    p4admin users [--days 90]
    p4admin streams [--depot //name] [--stale-days 90]
    p4admin scaffold <config.yaml> [--dry-run]
    p4admin typemap <template> [--dry-run]
    p4admin permissions <template> <production> [--dry-run]
    p4admin --help

All commands support:
    --config FILE     Path to YAML config file
    --output FILE     Save report to file (format auto-detected from extension)
    --format FORMAT   Output format: terminal, json, markdown, html
    --verbose         Enable debug logging
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from p4admin.core.config import load_config
from p4admin.core.connection import P4Connection, P4ConnectionError
from p4admin.core.output import (
    OutputFormat,
    render_terminal,
    render_json,
    render_markdown,
    save_report,
)

console = Console()


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ============================================================
# Main CLI group
# ============================================================

@click.group()
@click.option("--config", "config_path", type=click.Path(exists=True), help="Path to YAML config file")
@click.option("--verbose", is_flag=True, help="Enable debug logging")
@click.version_option(package_name="p4-admin-utils")
@click.pass_context
def cli(ctx, config_path, verbose):
    """
    p4admin — Perforce administration utilities for creative studios.

    Tools for managing workspaces, licenses, depots, and server health
    in entertainment production environments.
    """
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path)


# ============================================================
# Cleanup commands
# ============================================================

@cli.command()
@click.option("--stale-days", default=90, show_default=True, help="Days without access before workspace is stale")
@click.option("--cleanup", is_flag=True, help="Delete stale workspaces (use with --dry-run first!)")
@click.option("--dry-run", is_flag=True, help="Preview deletions without actually deleting")
@click.option("--force", is_flag=True, help="Also delete workspaces with pending work (dangerous)")
@click.option("--exclude", multiple=True, help="Workspace name patterns to exclude (e.g., 'build-*')")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def workspaces(ctx, stale_days, cleanup, dry_run, force, exclude, output_path, output_format):
    """
    Find and clean up stale workspaces.

    Identifies workspaces that haven't been accessed in --stale-days,
    workspaces whose owners no longer exist, and workspaces with
    orphaned shelved changes.

    \b
    Examples:
        p4admin workspaces                        # Report stale workspaces (90 day default)
        p4admin workspaces --stale-days 60         # Use 60-day threshold
        p4admin workspaces --exclude 'build-*'     # Skip CI workspaces
        p4admin workspaces --cleanup --dry-run     # Preview what would be deleted
        p4admin workspaces --cleanup               # Actually delete stale workspaces
        p4admin workspaces --output report.html    # Save HTML report
    """
    from p4admin.cleanup.stale_workspaces import StaleWorkspaceFinder

    config = ctx.obj["config"]
    exclude_patterns = list(exclude) if exclude else config.get("thresholds", {}).get("exclude_patterns", [])

    try:
        with P4Connection.from_config(config) as conn:
            finder = StaleWorkspaceFinder(
                conn=conn,
                stale_days=stale_days,
                exclude_patterns=exclude_patterns,
            )

            if cleanup:
                if force and not dry_run:
                    if not click.confirm(
                        "WARNING: --force will delete workspaces with pending changes. Continue?",
                        default=False,
                    ):
                        console.print("[yellow]Aborted.[/yellow]")
                        return

                deleted = finder.cleanup(dry_run=dry_run, force=force)
                action = "Would delete" if dry_run else "Deleted"
                console.print(f"\n[bold]{action} {len(deleted)} workspaces[/bold]")
                if dry_run and deleted:
                    console.print("[dim]Run without --dry-run to execute.[/dim]")
            else:
                report = finder.analyze()
                _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--stale-days", default=60, show_default=True, help="Days since modification before a shelf is stale")
@click.option("--cleanup", is_flag=True, help="Delete orphaned shelves")
@click.option("--dry-run", is_flag=True, help="Preview deletions without actually deleting")
@click.option("--owner-gone-only", is_flag=True, help="Only delete shelves where the owner no longer exists")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def shelves(ctx, stale_days, cleanup, dry_run, owner_gone_only, output_path, output_format):
    """
    Find and clean up orphaned shelved changelists.

    Identifies shelved changes from users who no longer exist or
    shelves that haven't been touched in --stale-days.

    \b
    Examples:
        p4admin shelves                            # Report orphaned shelves
        p4admin shelves --stale-days 30            # 30-day threshold
        p4admin shelves --cleanup --dry-run        # Preview cleanup
        p4admin shelves --cleanup --owner-gone-only # Only clean up if owner is gone
    """
    from p4admin.cleanup.orphaned_shelves import OrphanedShelfCleaner

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            cleaner = OrphanedShelfCleaner(
                conn=conn,
                stale_days=stale_days,
            )

            if cleanup:
                deleted = cleaner.cleanup(dry_run=dry_run, owner_gone_only=owner_gone_only)
                action = "Would delete" if dry_run else "Deleted"
                console.print(f"\n[bold]{action} {len(deleted)} shelved changelists[/bold]")
                if dry_run and deleted:
                    console.print("[dim]Run without --dry-run to execute.[/dim]")
            else:
                report = cleaner.analyze()
                _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--min-age", default=7, show_default=True, help="Minimum age in days before considering a changelist empty")
@click.option("--cleanup", is_flag=True, help="Delete empty changelists")
@click.option("--dry-run", is_flag=True, help="Preview deletions without actually deleting")
@click.option("--exclude-user", multiple=True, help="Users to exclude (service accounts, etc.)")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def changelists(ctx, min_age, cleanup, dry_run, exclude_user, output_path, output_format):
    """
    Find and prune empty pending changelists.

    Identifies pending changelists with no open files and no shelved
    content. These accumulate from reverted changes and failed submits.

    \b
    Examples:
        p4admin changelists                        # Report empty changelists
        p4admin changelists --min-age 30           # Only show 30+ day old empties
        p4admin changelists --cleanup --dry-run    # Preview cleanup
        p4admin changelists --exclude-user build   # Skip build service account
    """
    from p4admin.cleanup.empty_changelists import EmptyChangelistPruner

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            pruner = EmptyChangelistPruner(
                conn=conn,
                min_age_days=min_age,
                exclude_users=list(exclude_user),
            )

            if cleanup:
                deleted = pruner.cleanup(dry_run=dry_run)
                action = "Would delete" if dry_run else "Deleted"
                console.print(f"\n[bold]{action} {len(deleted)} empty changelists[/bold]")
                if dry_run and deleted:
                    console.print("[dim]Run without --dry-run to execute.[/dim]")
            else:
                report = pruner.analyze()
                _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)


# ============================================================
# Reporting commands
# ============================================================

@cli.command()
@click.option("--inactive-days", default=90, show_default=True, help="Days without activity before a user is inactive")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def health(ctx, inactive_days, output_path, output_format):
    """
    Server health and license dashboard.

    Shows license status, seat utilization, inactive users,
    server version info, and support contacts.

    \b
    Examples:
        p4admin health                            # Terminal dashboard
        p4admin health --inactive-days 60          # 60-day inactivity threshold
        p4admin health --output status.html        # Save HTML report
        p4admin health --format json               # JSON output (for automation)
    """
    from p4admin.licensing.health_check import ServerHealthCheck

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            checker = ServerHealthCheck(
                conn=conn,
                config=config,
                inactive_days=inactive_days,
            )
            report = checker.analyze()
            _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)


@cli.command("depot-size")
@click.option("--depot", default=None, help="Specific depot to analyze (e.g., //assets)")
@click.option("--top-paths", default=15, show_default=True, help="Number of largest paths to show")
@click.option("--top-users", default=10, show_default=True, help="Number of top storage consumers")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def depot_size(ctx, depot, top_paths, top_users, output_path, output_format):
    """
    Analyze depot storage usage.

    Shows per-depot size breakdown, largest paths, and storage
    consumers. Answers "why is the server using so much disk?"

    \b
    Examples:
        p4admin depot-size                         # All depots
        p4admin depot-size --depot //assets         # Specific depot
        p4admin depot-size --top-paths 25           # More detail
        p4admin depot-size --output storage.html    # HTML report
    """
    from p4admin.reporting.depot_size import DepotSizeAnalyzer

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            analyzer = DepotSizeAnalyzer(
                conn=conn,
                top_paths=top_paths,
                top_users=top_users,
            )
            report = analyzer.analyze(depot=depot)
            _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--days", default=90, show_default=True, help="Analysis window in days")
@click.option("--no-groups", is_flag=True, help="Skip group membership lookup (faster)")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def users(ctx, days, no_groups, output_path, output_format):
    """
    User activity report.

    Shows who's active, inactive, and never accessed. Useful for
    license optimization and identifying onboarding issues.

    \b
    Examples:
        p4admin users                              # 90-day activity window
        p4admin users --days 30                    # Last 30 days
        p4admin users --no-groups                  # Skip group lookup (faster)
        p4admin users --output users.html          # HTML report
    """
    from p4admin.reporting.user_activity import UserActivityReport

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            reporter = UserActivityReport(
                conn=conn,
                days=days,
                include_groups=not no_groups,
            )
            report = reporter.analyze()
            _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--depot", default=None, help="Specific stream depot to analyze")
@click.option("--stale-days", default=90, show_default=True, help="Days without activity before a stream is stale")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def streams(ctx, depot, stale_days, output_path, output_format):
    """
    Stream hierarchy health check.

    Identifies stale task streams, orphaned streams with no workspaces,
    deeply nested hierarchies, and streams that need cleanup.

    \b
    Examples:
        p4admin streams                            # All stream depots
        p4admin streams --depot //film-assets       # Specific depot
        p4admin streams --stale-days 60            # 60-day threshold
        p4admin streams --output streams.html      # HTML report
    """
    from p4admin.reporting.stream_health import StreamHealthCheck

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            checker = StreamHealthCheck(
                conn=conn,
                stale_days=stale_days,
            )
            report = checker.analyze(depot=depot)
            _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)


# ============================================================
# Setup commands
# ============================================================

@cli.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, default=True, show_default=True, help="Preview without creating (default: true)")
@click.option("--apply", "do_apply", is_flag=True, help="Actually create infrastructure (disables dry-run)")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def scaffold(ctx, config_file, dry_run, do_apply, output_path, output_format):
    """
    Scaffold Perforce infrastructure for a new production.

    Reads a YAML config defining depot, streams, groups, and protections,
    then creates everything. Idempotent — safe to run again.

    \b
    Examples:
        p4admin scaffold configs/new-film.yaml              # Preview (dry-run)
        p4admin scaffold configs/new-film.yaml --apply       # Create everything
        p4admin scaffold configs/new-film.yaml --output plan.html  # Save plan
    """
    from p4admin.setup.scaffolding import ProductionScaffolder

    config = ctx.obj["config"]
    actual_dry_run = not do_apply

    try:
        with P4Connection.from_config(config) as conn:
            scaffolder = ProductionScaffolder(conn=conn)

            if actual_dry_run:
                report = scaffolder.preview(config_file)
            else:
                if not click.confirm("Create all production infrastructure?", default=False):
                    console.print("[yellow]Aborted.[/yellow]")
                    return
                report = scaffolder.apply(config_file, dry_run=False)

            _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Config error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("template", required=False)
@click.option("--list", "list_templates", is_flag=True, help="List available typemap templates")
@click.option("--dry-run", is_flag=True, default=True, show_default=True, help="Preview without applying")
@click.option("--apply", "do_apply", is_flag=True, help="Actually apply the typemap (disables dry-run)")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def typemap(ctx, template, list_templates, dry_run, do_apply, output_path, output_format):
    """
    Manage typemap templates for creative pipelines.

    Apply curated typemap configurations for VFX, games, or post-production.
    Merges with existing typemap — won't duplicate entries.

    \b
    Available templates: vfx, games, post

    \b
    Examples:
        p4admin typemap --list                     # List available templates
        p4admin typemap vfx                        # Preview VFX typemap
        p4admin typemap games --apply              # Apply games typemap
        p4admin typemap post --output typemap.md   # Save comparison to file
    """
    from p4admin.setup.typemaps import TypemapManager

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            manager = TypemapManager(conn=conn)

            if list_templates:
                templates = manager.list_templates()
                console.print("\n[bold]Available Typemap Templates[/bold]\n")
                for name, desc in templates.items():
                    console.print(f"  [cyan]{name}[/cyan] — {desc}")
                console.print()
                return

            if not template:
                console.print("[red]Specify a template name or use --list[/red]")
                sys.exit(1)

            actual_dry_run = not do_apply
            if actual_dry_run:
                report = manager.preview(template)
            else:
                report = manager.apply(template, dry_run=False)

            _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("template", required=False)
@click.argument("production", required=False)
@click.option("--list", "list_templates", is_flag=True, help="List available permission templates")
@click.option("--dry-run", is_flag=True, default=True, show_default=True, help="Preview without creating")
@click.option("--apply", "do_apply", is_flag=True, help="Actually create groups (disables dry-run)")
@click.option("--output", "output_path", type=click.Path(), help="Save report to file")
@click.option("--format", "output_format", type=click.Choice(["terminal", "json", "markdown", "html"]), default="terminal")
@click.pass_context
def permissions(ctx, template, production, list_templates, dry_run, do_apply, output_path, output_format):
    """
    Create permission groups from role-based templates.

    Generates standard P4 group hierarchies mapped to production roles.
    Templates available for film, games, and post-production.

    \b
    Available templates: film, games, post

    \b
    Examples:
        p4admin permissions --list                          # List templates
        p4admin permissions film titan                       # Preview film groups for "titan"
        p4admin permissions games starfield --apply          # Create game groups
    """
    from p4admin.setup.permissions import PermissionTemplateManager

    config = ctx.obj["config"]

    try:
        with P4Connection.from_config(config) as conn:
            manager = PermissionTemplateManager(conn=conn)

            if list_templates:
                templates = manager.list_templates()
                console.print("\n[bold]Available Permission Templates[/bold]\n")
                for name, desc in templates.items():
                    console.print(f"  [cyan]{name}[/cyan] — {desc}")
                console.print()
                return

            if not template or not production:
                console.print("[red]Specify template and production name, or use --list[/red]")
                console.print("[dim]Usage: p4admin permissions <template> <production>[/dim]")
                sys.exit(1)

            actual_dry_run = not do_apply
            if actual_dry_run:
                report = manager.preview(template, production)
            else:
                report = manager.apply(template, production, dry_run=False)

            _output_report(report, output_path, output_format)

    except P4ConnectionError as e:
        console.print(f"[red]Connection error:[/red] {e}")
        sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# ============================================================
# Output helpers
# ============================================================

def _output_report(report, output_path, output_format):
    """Handle report output to terminal and/or file."""
    if output_format == "terminal" or not output_path:
        render_terminal(report, console)

    if output_format == "json" and not output_path:
        console.print(render_json(report))

    if output_path:
        save_report(report, output_path, output_format if output_format != "terminal" else None)
        console.print(f"\n[green]Report saved to:[/green] {output_path}")


def main():
    """Entry point for direct script execution."""
    cli()


if __name__ == "__main__":
    main()
