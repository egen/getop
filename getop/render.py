"""Shared Rich rendering helpers for getop.

All commands render through these helpers so output style stays consistent
and --json is handled uniformly.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

SEVERITY_STYLES: dict[str, str] = {
    "DEFAULT": "dim",
    "DEBUG": "dim",
    "INFO": "cyan",
    "NOTICE": "cyan",
    "WARNING": "yellow",
    "ERROR": "bold red",
    "CRITICAL": "bold white on red",
    "ALERT": "bold white on red",
    "EMERGENCY": "bold white on red",
}


def severity_style(severity: str | None) -> str:
    """Rich style string for a Cloud Logging severity name."""
    return SEVERITY_STYLES.get((severity or "DEFAULT").upper(), "white")


def _cell(value: Any) -> Any:
    """Rich renderables (Text, Spinner, …) pass through; everything else is str'd."""
    if value is None:
        return ""
    if hasattr(value, "__rich_console__") or hasattr(value, "__rich__"):
        return value
    return str(value)


def table(title: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> Table:
    """Build a consistently styled Rich table."""
    t = Table(title=title, title_style="bold", header_style="bold blue", expand=False)
    for col in columns:
        t.add_column(col, overflow="fold")
    for row in rows:
        t.add_row(*(_cell(cell) for cell in row))
    return t


def warn_banner(message: str) -> None:
    """Print a prominent warning banner (used before sensitive output).

    Goes to stderr so it never corrupts --json output on stdout.
    """
    err_console.print(
        Panel(
            f"[bold yellow]⚠  {message}[/bold yellow]",
            border_style="yellow",
            title="[bold yellow]SENSITIVE[/bold yellow]",
        )
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def emit_json(data: Any) -> None:
    """Print machine-readable JSON to stdout."""
    print(json.dumps(data, indent=2, default=_json_default))


def output(data: Any, renderable: Any, as_json: bool) -> None:
    """--json passthrough: emit `data` as JSON, otherwise print the Rich renderable."""
    if as_json:
        emit_json(data)
    else:
        console.print(renderable)
