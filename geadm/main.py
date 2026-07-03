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
    help="CLI for Google Gemini Enterprise.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@dataclass
class AppState:
    project: str | None
    location: str
    quota_project: str | None = None


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
    quota_project: str = typer.Option(
        None,
        "--quota-project",
        help=(
            "Project to bill API quota against (defaults to --project). Useful "
            "when you lack serviceusage.services.use on the target project."
        ),
        envvar="GOOGLE_CLOUD_QUOTA_PROJECT",
    ),
) -> None:
    """CLI for Google Gemini Enterprise."""
    ctx.obj = AppState(project=project, location=location, quota_project=quota_project)


# ---- command groups (implemented in geadm/commands/) -----------------------

from geadm.commands import doctor as doctor_cmd  # noqa: E402
from geadm.commands import info as info_cmd  # noqa: E402
from geadm.commands import logs as logs_cmd  # noqa: E402
from geadm.commands import ls as ls_cmd  # noqa: E402
from geadm.commands import stats as stats_cmd  # noqa: E402

app.add_typer(ls_cmd.app, name="ls")
app.add_typer(logs_cmd.app, name="logs")
app.command(name="stats")(stats_cmd.stats_command)
app.command(name="quota")(stats_cmd.quota_command)
app.command(name="doctor")(doctor_cmd.doctor_command)
app.command(name="info")(info_cmd.info_command)


def run() -> None:
    """Console entrypoint: run the app with concise permission-error reporting."""
    import warnings

    from google.api_core.exceptions import PermissionDenied
    from requests import HTTPError

    from geadm.render import err_console

    # google.auth warns that user ADC has no quota project; geadm sets one
    # itself (see auth.Clients._credentials), so the warning is just noise.
    warnings.filterwarnings(
        "ignore",
        message=".*end user credentials.*without a quota project.*",
        category=UserWarning,
    )
    # The API can return enum values newer than the published client knows
    # (e.g. preview ContentConfig values); geadm renders them numerically.
    warnings.filterwarnings(
        "ignore",
        message=r"Unrecognized \w+ enum value.*",
        category=UserWarning,
    )

    try:
        app()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except PermissionDenied as exc:
        message = (getattr(exc, "message", None) or str(exc)).splitlines()[0]
        err_console.print(f"[bold red]Permission denied:[/bold red] {message}")
        if "serviceusage" in message or "USER_PROJECT_DENIED" in str(exc.errors):
            err_console.print(
                "[yellow]Hint:[/yellow] your ADC user credentials must bill API "
                "quota to a project where you hold serviceusage.services.use. "
                "Pass [bold]--quota-project <project>[/bold] (or set "
                "GOOGLE_CLOUD_QUOTA_PROJECT) to bill a project you can use."
            )
        else:
            err_console.print(
                "[yellow]Hint:[/yellow] geadm needs roles/discoveryengine.viewer, "
                "roles/logging.viewer and roles/monitoring.viewer on the target "
                "project."
            )
        raise SystemExit(1) from None
    except HTTPError as exc:
        response = exc.response
        err_console.print(f"[bold red]API error:[/bold red] {exc}")
        if response is not None and response.status_code == 403:
            err_console.print(
                "[yellow]Hint:[/yellow] check roles/discoveryengine.viewer on the "
                "target project, or --quota-project if the error mentions quota."
            )
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
