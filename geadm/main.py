"""geadm — read-only troubleshooting / debug / stats CLI for Google Gemini Enterprise.

Targets the Discovery Engine / Agentspace product (discoveryengine.googleapis.com).
Strictly read-only: needs only roles/discoveryengine.viewer, roles/logging.viewer
and roles/monitoring.viewer. Auth is Application Default Credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

import typer

app = typer.Typer(
    name="geadm",
    help=__doc__,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@dataclass
class AppState:
    project: str | None
    location: str


@app.callback()
def main(
    ctx: typer.Context,
    project: str = typer.Option(
        None,
        "--project",
        "-p",
        help="GCP project ID (defaults to the ADC project).",
        envvar="GOOGLE_CLOUD_PROJECT",
    ),
    location: str = typer.Option(
        "global",
        "--location",
        "-l",
        help="Gemini Enterprise location (e.g. global, us, eu).",
    ),
) -> None:
    """Read-only troubleshooting CLI for Google Gemini Enterprise."""
    ctx.obj = AppState(project=project, location=location)


# ---- command groups (implemented in geadm/commands/) -----------------------

from geadm.commands import doctor as doctor_cmd  # noqa: E402
from geadm.commands import logs as logs_cmd  # noqa: E402
from geadm.commands import ls as ls_cmd  # noqa: E402
from geadm.commands import stats as stats_cmd  # noqa: E402

app.add_typer(ls_cmd.app, name="ls")
app.add_typer(logs_cmd.app, name="logs")
app.command(name="stats")(stats_cmd.stats_command)
app.command(name="doctor")(doctor_cmd.doctor_command)


if __name__ == "__main__":
    app()
