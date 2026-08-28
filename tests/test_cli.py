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


# --------------------------------------------------------------------------
# diff / convert
# --------------------------------------------------------------------------

CDIR = REPO / "samples" / "container"
CSHEET = CDIR / "container.gcs"
CPLAYED = CDIR / "container-played.foundry.json"
CCONTROL = CDIR / "container.foundry.json"


def test_diff_reports_the_session(capsys):
    code, out = run(capsys, "diff", str(CPLAYED), "--base", str(CSHEET))
    assert code == 0
    assert "Changes to carry back" in out
    assert "The Book of Metabackpacking" in out
    assert "Poisons" in out


def test_diff_writes_nothing(capsys, tmp_path):
    import shutil as _shutil

    sheet = tmp_path / "s.gcs"
    _shutil.copy(CSHEET, sheet)
    before = sheet.read_bytes()
    run(capsys, "diff", str(CPLAYED), "--base", str(sheet))
    assert sheet.read_bytes() == before


def test_diff_without_a_base_is_an_error(capsys, tmp_path):
    import shutil as _shutil

    export = tmp_path / "e.json"
    _shutil.copy(CPLAYED, export)
    assert cli.main(["diff", str(export)]) == 2
    assert "no base .gcs sheet" in capsys.readouterr().err


def test_convert_writes_a_merged_sheet(capsys, tmp_path):
    out = tmp_path / "merged.gcs"
    code, text = run(
        capsys, "convert", str(CPLAYED), "--base", str(CSHEET), "-o", str(out)
    )
    assert code == 0
    assert out.exists()
    assert "Wrote" in text

    from json2gcs import gcs as gcsmod

    merged = gcsmod.load(out)
    assert str(merged.by_tid["eQvR7mN2xLkT4bH9c"].data["quantity"]) == "4"
    assert merged.by_tid["eMNPcBPVkCDd_WRHP"].data["description"] == (
        "The Book of Metabackpacking"
    )


def test_convert_defaults_the_output_beside_the_base(capsys, tmp_path):
    import shutil as _shutil

    sheet = tmp_path / "hero.gcs"
    _shutil.copy(CSHEET, sheet)
    code, _ = run(capsys, "convert", str(CPLAYED), "--base", str(sheet))
    assert code == 0
    assert (tmp_path / "hero.merged.gcs").exists()
    assert sheet.read_bytes() == CSHEET.read_bytes(), "the base must be untouched"


def test_convert_refuses_to_overwrite_the_base(capsys, tmp_path):
    import shutil as _shutil

    sheet = tmp_path / "hero.gcs"
    _shutil.copy(CSHEET, sheet)
    assert (
        cli.main(["convert", str(CPLAYED), "--base", str(sheet), "-o", str(sheet)]) == 2
    )
    assert "refusing to overwrite" in capsys.readouterr().err
    assert sheet.read_bytes() == CSHEET.read_bytes()


def test_convert_force_allows_overwriting(capsys, tmp_path):
    import shutil as _shutil

    sheet = tmp_path / "hero.gcs"
    _shutil.copy(CSHEET, sheet)
    code, _ = run(
        capsys,
        "convert",
        str(CPLAYED),
        "--base",
        str(sheet),
        "-o",
        str(sheet),
        "--force",
    )
    assert code == 0
    assert sheet.read_bytes() != CSHEET.read_bytes()


def test_convert_dry_run_writes_nothing(capsys, tmp_path):
    out = tmp_path / "merged.gcs"
    code, text = run(
        capsys,
        "convert",
        str(CPLAYED),
        "--base",
        str(CSHEET),
        "-o",
        str(out),
        "--dry-run",
    )
    assert code == 0
    assert not out.exists()
    assert "Dry run" in text and "Nothing written" in text


def test_convert_on_a_control_export_is_a_no_op(capsys, tmp_path):
    out = tmp_path / "merged.gcs"
    code, text = run(
        capsys, "convert", str(CCONTROL), "--base", str(CSHEET), "-o", str(out)
    )
    assert code == 0
    assert "Nothing to carry back" in text
    assert not out.exists()


def test_rename_carries_the_actor_name_back(capsys, tmp_path):
    out = tmp_path / "merged.gcs"
    code, text = run(
        capsys,
        "convert",
        str(CCONTROL),
        "--base",
        str(CSHEET),
        "-o",
        str(out),
        "--rename",
    )
    assert code == 0
    assert "0 new row(s)" in text and "0 dropped" in text
    assert json.loads(out.read_text(encoding="utf-8"))["profile"]["name"] == "Container"


def test_convert_can_drop_ambiguous_deletions(capsys, tmp_path):
    out = tmp_path / "merged.gcs"
    run(
        capsys,
        "convert",
        str(CPLAYED),
        "--base",
        str(CSHEET),
        "-o",
        str(out),
        "--deletions",
        "drop",
    )
    from json2gcs import gcs as gcsmod

    assert "sU3-t1E9yl1E581hQ" not in gcsmod.load(out).by_tid
