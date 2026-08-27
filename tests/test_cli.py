"""Tests for the command line entry point."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from json2gcs import cli

REPO = Path(__file__).resolve().parent.parent
EXPORT = REPO / "samples" / "sturm" / "sturm.foundry.json"
SHEET = REPO / "samples" / "sturm" / "sturm.gcs"


def run(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_inspect_reports_the_sample(capsys):
    code, out = run(capsys, "inspect", str(EXPORT), "--base", str(SHEET))
    assert code == 0
    assert "Stürm" in out
    assert "0.18.13" in out
    assert "matched by TID    : 70" in out
    assert "only in Foundry   : 0" in out
    assert "only in base sheet: 3" in out
    # The three ambiguous rows are named, not just counted.
    for name in ("Tracking", "Jumping", "Climbing"):
        assert name in out
    assert "ambiguous" in out
    assert "Warnings" not in out


def test_inspect_without_base(capsys):
    code, out = run(capsys, "inspect", str(EXPORT))
    assert code == 0
    assert "matched by TID" not in out
    # The fixture was renamed, so auto-detection correctly finds nothing.
    assert "not found" in out
    assert "Stürm.gcs" in out


def test_inspect_auto_detects_base_from_importname(capsys, tmp_path: Path):
    """system.additionalresources.importname names the original file."""
    shutil.copy(EXPORT, tmp_path / "actor.json")
    shutil.copy(SHEET, tmp_path / "Stürm.gcs")
    code, out = run(capsys, "inspect", str(tmp_path / "actor.json"))
    assert code == 0
    assert "auto-detected" in out
    assert "matched by TID    : 70" in out


def test_inspect_exits_nonzero_on_warnings(capsys, tmp_path: Path):
    export = tmp_path / "items.json"
    export.write_text(
        json.dumps(
            {
                "name": "T",
                "type": "character",
                "system": {},
                "items": [{"name": "An Item"}],
            }
        ),
        encoding="utf-8",
    )
    code, out = run(capsys, "inspect", str(export))
    assert code == 1
    assert "use Foundry items" in out


def test_missing_file_is_reported_not_traced(capsys, tmp_path: Path):
    code = cli.main(["inspect", str(tmp_path / "nope.json")])
    assert code == 2
    assert "json2gcs:" in capsys.readouterr().err


def test_bad_json_is_reported_not_traced(capsys, tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": "world"}', encoding="utf-8")
    assert cli.main(["inspect", str(bad)]) == 2
    assert "does not look like a Foundry actor" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "json2gcs" in capsys.readouterr().out


def test_command_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
