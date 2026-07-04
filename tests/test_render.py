import json

from rich.text import Text

from getop import render


def test_severity_style_known_and_unknown():
    assert render.severity_style("ERROR") == "bold red"
    assert render.severity_style("info") == "cyan"
    assert render.severity_style(None) == "dim"
    assert render.severity_style("NONSENSE") == "white"


def test_table_passes_rich_renderables_through():
    t = render.table("T", ["a", "b"], [[Text("styled"), None], ["plain", 42]])
    assert t.row_count == 2


def test_emit_json_handles_datetimes(capsys):
    from datetime import datetime, timezone

    render.emit_json({"ts": datetime(2026, 1, 1, tzinfo=timezone.utc)})
    out = json.loads(capsys.readouterr().out)
    assert out["ts"].startswith("2026-01-01")


def test_output_json_mode_is_pure_stdout(capsys):
    render.output([{"x": 1}], Text("table"), as_json=True)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [{"x": 1}]


def test_warn_banner_goes_to_stderr(capsys):
    render.warn_banner("careful now")
    captured = capsys.readouterr()
    assert "careful now" in captured.err
    assert captured.out == ""
