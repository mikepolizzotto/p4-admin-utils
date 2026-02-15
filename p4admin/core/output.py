"""
Output formatting for all p4admin tools.

Supports multiple output formats so tools can write once and
render to terminal (with Rich), JSON, markdown, or HTML.

Usage:
    formatter = get_formatter("terminal")
    formatter.heading("Stale Workspaces Report")
    formatter.table(headers, rows)
    formatter.stat("Total stale", 42, style="warning")
    formatter.save("report.md", format="markdown")
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class OutputFormat(str, Enum):
    TERMINAL = "terminal"
    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"


@dataclass
class ReportData:
    """
    Structured report data that can be rendered in any format.

    Tools build a ReportData object, then pass it to a formatter.
    This decouples data gathering from presentation.
    """

    title: str
    generated_at: datetime = field(default_factory=datetime.now)
    server_info: dict[str, str] = field(default_factory=dict)
    sections: list[ReportSection] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def add_section(
        self,
        title: str,
        headers: list[str] | None = None,
        rows: list[list[str]] | None = None,
        stats: dict[str, Any] | None = None,
        notes: list[str] | None = None,
    ) -> ReportSection:
        section = ReportSection(
            title=title,
            headers=headers or [],
            rows=rows or [],
            stats=stats or {},
            notes=notes or [],
        )
        self.sections.append(section)
        return section

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dictionary for JSON serialization."""
        return {
            "title": self.title,
            "generated_at": self.generated_at.isoformat(),
            "server_info": self.server_info,
            "summary": self.summary,
            "sections": [
                {
                    "title": s.title,
                    "headers": s.headers,
                    "rows": s.rows,
                    "stats": s.stats,
                    "notes": s.notes,
                }
                for s in self.sections
            ],
        }


@dataclass
class ReportSection:
    title: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def render_terminal(report: ReportData, console: Console | None = None):
    """Render a report to the terminal using Rich."""
    console = console or Console()

    # Title
    console.print()
    console.print(Panel(f"[bold]{report.title}[/bold]", border_style="blue"))
    console.print(f"  [dim]Generated: {report.generated_at:%Y-%m-%d %H:%M:%S}[/dim]")

    # Server info
    if report.server_info:
        console.print(f"  [dim]Server: {report.server_info.get('serverAddress', 'N/A')}[/dim]")
        console.print()

    # Summary stats at the top
    if report.summary:
        summary_table = Table(show_header=False, box=None, padding=(0, 2))
        summary_table.add_column("Key", style="bold")
        summary_table.add_column("Value")
        for key, value in report.summary.items():
            style = _stat_style(key, value)
            summary_table.add_row(key, Text(str(value), style=style))
        console.print(Panel(summary_table, title="Summary", border_style="cyan"))
        console.print()

    # Sections
    for section in report.sections:
        console.print(f"[bold cyan]{section.title}[/bold cyan]")
        console.print("─" * 60)

        # Section stats
        if section.stats:
            for key, value in section.stats.items():
                style = _stat_style(key, value)
                console.print(f"  {key}: [{style}]{value}[/{style}]")
            console.print()

        # Table data
        if section.headers and section.rows:
            table = Table(show_lines=False, padding=(0, 1))
            for header in section.headers:
                table.add_column(header, style="bold")
            for row in section.rows:
                table.add_row(*[str(cell) for cell in row])
            console.print(table)
            console.print()

        # Notes
        for note in section.notes:
            console.print(f"  [dim italic]{note}[/dim italic]")

        console.print()


def render_json(report: ReportData) -> str:
    """Render a report as JSON."""
    return json.dumps(report.to_dict(), indent=2, default=str)


def render_markdown(report: ReportData) -> str:
    """Render a report as Markdown."""
    lines = []
    lines.append(f"# {report.title}")
    lines.append(f"*Generated: {report.generated_at:%Y-%m-%d %H:%M:%S}*")
    lines.append("")

    if report.server_info:
        lines.append(f"**Server:** {report.server_info.get('serverAddress', 'N/A')}")
        lines.append("")

    if report.summary:
        lines.append("## Summary")
        for key, value in report.summary.items():
            lines.append(f"- **{key}:** {value}")
        lines.append("")

    for section in report.sections:
        lines.append(f"## {section.title}")
        lines.append("")

        if section.stats:
            for key, value in section.stats.items():
                lines.append(f"- **{key}:** {value}")
            lines.append("")

        if section.headers and section.rows:
            # Markdown table
            lines.append("| " + " | ".join(section.headers) + " |")
            lines.append("| " + " | ".join("---" for _ in section.headers) + " |")
            for row in section.rows:
                lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
            lines.append("")

        for note in section.notes:
            lines.append(f"> {note}")
        lines.append("")

    return "\n".join(lines)


def render_html(report: ReportData) -> str:
    """Render a report as a standalone HTML file."""
    sections_html = []

    for section in report.sections:
        section_parts = [f"<h2>{section.title}</h2>"]

        if section.stats:
            stats_html = "".join(
                f"<div class='stat'><span class='stat-label'>{k}:</span> "
                f"<span class='stat-value'>{v}</span></div>"
                for k, v in section.stats.items()
            )
            section_parts.append(f"<div class='stats-grid'>{stats_html}</div>")

        if section.headers and section.rows:
            headers = "".join(f"<th>{h}</th>" for h in section.headers)
            rows = "".join(
                "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
                for row in section.rows
            )
            section_parts.append(
                f"<table><thead><tr>{headers}</tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )

        for note in section.notes:
            section_parts.append(f"<p class='note'>{note}</p>")

        sections_html.append(f"<section>{''.join(section_parts)}</section>")

    summary_html = ""
    if report.summary:
        items = "".join(
            f"<div class='summary-item'><span class='label'>{k}</span>"
            f"<span class='value'>{v}</span></div>"
            for k, v in report.summary.items()
        )
        summary_html = f"<div class='summary-grid'>{items}</div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{report.title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117; color: #c9d1d9;
            max-width: 1200px; margin: 0 auto; padding: 2rem;
        }}
        h1 {{ color: #58a6ff; margin-bottom: 0.5rem; font-size: 1.8rem; }}
        h2 {{ color: #79c0ff; margin: 1.5rem 0 1rem; font-size: 1.3rem;
               border-bottom: 1px solid #21262d; padding-bottom: 0.5rem; }}
        .meta {{ color: #8b949e; font-size: 0.9rem; margin-bottom: 1.5rem; }}
        .summary-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem; margin: 1rem 0;
        }}
        .summary-item {{
            background: #161b22; border: 1px solid #21262d; border-radius: 8px;
            padding: 1rem; text-align: center;
        }}
        .summary-item .label {{ display: block; color: #8b949e; font-size: 0.85rem; }}
        .summary-item .value {{ display: block; font-size: 1.5rem; font-weight: bold;
                                 color: #58a6ff; margin-top: 0.25rem; }}
        .stats-grid {{ margin: 0.5rem 0; }}
        .stat {{ padding: 0.25rem 0; }}
        .stat-label {{ color: #8b949e; }}
        .stat-value {{ color: #c9d1d9; font-weight: 600; }}
        table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
        th {{ background: #161b22; color: #58a6ff; text-align: left;
              padding: 0.75rem; border-bottom: 2px solid #21262d; }}
        td {{ padding: 0.75rem; border-bottom: 1px solid #21262d; }}
        tr:hover {{ background: #161b22; }}
        .note {{ color: #8b949e; font-style: italic; margin: 0.5rem 0; }}
        section {{ margin-bottom: 2rem; }}
    </style>
</head>
<body>
    <h1>{report.title}</h1>
    <div class="meta">
        Generated: {report.generated_at:%Y-%m-%d %H:%M:%S}
        {f" | Server: {report.server_info.get('serverAddress', '')}" if report.server_info else ""}
    </div>
    {summary_html}
    {''.join(sections_html)}
</body>
</html>"""


def save_report(report: ReportData, path: str | Path, fmt: OutputFormat | str | None = None):
    """
    Save a report to a file, auto-detecting format from extension if not specified.
    """
    path = Path(path)
    if fmt is None:
        ext_map = {".json": OutputFormat.JSON, ".md": OutputFormat.MARKDOWN, ".html": OutputFormat.HTML}
        fmt = ext_map.get(path.suffix, OutputFormat.MARKDOWN)
    elif isinstance(fmt, str):
        fmt = OutputFormat(fmt)

    renderers = {
        OutputFormat.JSON: render_json,
        OutputFormat.MARKDOWN: render_markdown,
        OutputFormat.HTML: render_html,
    }

    content = renderers[fmt](report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _stat_style(key: str, value: Any) -> str:
    """Determine Rich style based on stat context."""
    key_lower = key.lower()
    if any(word in key_lower for word in ["warning", "stale", "orphan", "unused", "expired"]):
        return "yellow"
    if any(word in key_lower for word in ["error", "critical", "overdue", "exceeded"]):
        return "red"
    if any(word in key_lower for word in ["ok", "active", "healthy", "available"]):
        return "green"
    return "white"
