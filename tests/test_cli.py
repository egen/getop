"""CLI wiring smoke tests: every command exposes working --help."""

import pytest

from geadm.main import app


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["ls", "--help"],
        ["ls", "engines", "--help"],
        ["ls", "datastores", "--help"],
        ["ls", "connectors", "--help"],
        ["ls", "agents", "--help"],
        ["logs", "--help"],
        ["logs", "connector", "--help"],
        ["logs", "user", "--help"],
        ["stats", "--help"],
        ["quota", "--help"],
        ["doctor", "--help"],
    ],
)
def test_help_screens(app_runner, args):
    result = app_runner.invoke(app, args)
    assert result.exit_code == 0, result.output


def test_help_is_concise_no_iam_or_api_internals(app_runner):
    """Help output reads like a typical CLI: IAM roles, RPC names and API
    versions belong in the README, not --help."""
    combined = ""
    for args in (
        ["--help"],
        ["ls", "--help"],
        ["logs", "--help"],
        ["stats", "--help"],
        ["quota", "--help"],
        ["doctor", "--help"],
        ["info", "--help"],
    ):
        combined += app_runner.invoke(app, args).output
    for banned in ("roles/", "ServiceClient", "v1alpha", "read-only ("):
        assert banned not in combined, f"{banned!r} leaked into help output"


def test_logs_user_help_mentions_prompt_logging(app_runner):
    out = app_runner.invoke(app, ["logs", "user", "--help"]).output
    assert "prompt/response" in out
