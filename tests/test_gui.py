"""The window's decision logic, tested without opening a window.

The GUI's one job is to turn a form into the argument list the command line
would have taken — it decides nothing else, and everything downstream is the
same code the CLI runs.  That translation, and the auto-detection that makes
the tool "a few clicks", are pure functions for exactly this reason.

Nothing here constructs a ``tk.Tk``: a test suite that pops up windows is one
people stop running.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from json2gcs import cli, gui

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"
CONTROL = DIR / "container.foundry.json"
SHEET = DIR / "container.gcs"


# --------------------------------------------------------------------------
# the form becomes a command line
# --------------------------------------------------------------------------


def test_the_default_form_is_the_simplest_command():
    argv = gui.build_argv(gui.Options(export="actor.json", base="c.gcs"))
    assert argv == ["convert", "actor.json", "--base", "c.gcs"]


def test_synthesize_drops_the_base_rather_than_sending_both():
    """The CLI refuses both together, so the window must not send both."""
    argv = gui.build_argv(
        gui.Options(export="actor.json", base="c.gcs", synthesize=True)
    )
    assert "--synthesize" in argv
    assert "--base" not in argv


def test_every_option_reaches_the_command_line():
    argv = gui.build_argv(
        gui.Options(
            export="actor.json",
            base="c.gcs",
            output="out.gcs",
            gcs="gcs.exe",
            rename=True,
            include_lossy=True,
            drop_deletions=True,
            refresh_calc=True,
            verify=True,
            dry_run=True,
        )
    )
    assert argv == [
        "convert", "actor.json",
        "--base", "c.gcs",
        "-o", "out.gcs",
        "--gcs", "gcs.exe",
        "--deletions", "drop",
        "--include-lossy", "--rename", "--refresh-calc", "--verify", "--dry-run",
    ]


def test_options_left_alone_are_not_sent_at_all():
    """An unticked box must be absent, not passed as a falsy value."""
    argv = gui.build_argv(gui.Options(export="actor.json"))
    assert argv == ["convert", "actor.json"]


def test_what_the_window_builds_is_a_command_the_parser_accepts():
    """The strongest check available without running anything: argparse itself
    decides whether the window is speaking the CLI's language."""
    argv = gui.build_argv(
        gui.Options(
            export=str(CONTROL), base=str(SHEET), output="out.gcs",
            rename=True, verify=True,
        )
    )
    args = cli.build_parser().parse_args(argv)
    assert args.func is cli.cmd_convert
    assert args.base == str(SHEET) and args.rename and args.verify


def test_the_dry_run_the_preview_button_builds_writes_nothing(tmp_path, capsys):
    out = tmp_path / "out.gcs"
    argv = gui.build_argv(
        gui.Options(export=str(CONTROL), base=str(SHEET), output=str(out), dry_run=True)
    )
    assert cli.main(argv) == 0
    assert not out.exists()
    assert "Nothing written" in capsys.readouterr().out


# --------------------------------------------------------------------------
# choosing an export fills in the rest
# --------------------------------------------------------------------------


def test_a_sheet_beside_the_export_is_found_and_merge_is_chosen():
    found = gui.suggest(CONTROL)
    assert Path(found.base) == SHEET
    assert not found.synthesize
    assert Path(found.output).name == "container.merged.gcs"
    assert "found the base sheet" in found.status
    assert "Container" in found.status


def test_no_sheet_beside_the_export_falls_back_to_synthesize(tmp_path):
    lonely = tmp_path / "actor.foundry.json"
    lonely.write_text(CONTROL.read_text(encoding="utf-8"), encoding="utf-8")

    found = gui.suggest(lonely)
    assert found.base == ""
    assert found.synthesize
    assert Path(found.output) == tmp_path / "actor.foundry.gcs"
    assert "no base sheet found" in found.status
    assert "container.gcs" in found.status, "say what was looked for"


def test_the_suggested_output_is_never_the_base_sheet():
    found = gui.suggest(CONTROL)
    assert Path(found.output) != Path(found.base)


def test_an_unreadable_export_reports_rather_than_raises(tmp_path):
    """A wrong file chosen in a file dialog must not take the window down."""
    junk = tmp_path / "notes.json"
    junk.write_text('{"hello": true}', encoding="utf-8")

    found = gui.suggest(junk)
    assert found.base == "" and found.output == ""
    assert "Could not read that export" in found.status


def test_a_missing_file_reports_rather_than_raises(tmp_path):
    found = gui.suggest(tmp_path / "nope.json")
    assert "Could not read that export" in found.status


# --------------------------------------------------------------------------
# packaging
# --------------------------------------------------------------------------


def test_running_with_no_arguments_opens_the_window(monkeypatch):
    """What a double-clicked executable does. The CLI is otherwise unchanged."""
    opened = []
    monkeypatch.setattr(cli.sys, "argv", ["json2gcs"])
    monkeypatch.setattr(cli, "cmd_gui", lambda args: opened.append(True) or 0)
    assert cli.main() == 0
    assert opened == [True]


def test_an_explicit_command_still_wins(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["json2gcs"])
    assert cli.main(["inspect", str(CONTROL)]) == 0
    assert "Container" in capsys.readouterr().out


def test_the_spec_bundles_the_synthesize_template():
    """--synthesize cannot run without data/default.gcs, and a plain --onefile
    invocation would leave it out. Guard the one line that prevents that."""
    spec = (REPO / "json2gcs.spec").read_text(encoding="utf-8")
    assert 'collect_data_files("json2gcs", includes=["data/*.gcs"])' in spec
    assert 'hiddenimports=["json2gcs.gui"]' in spec, (
        "gui is imported late inside cmd_gui, so PyInstaller cannot see it"
    )


def test_the_packaged_entry_point_does_not_use_a_relative_import():
    """PyInstaller runs __main__.py as a top-level script with no package, so a
    relative import here builds cleanly and then fails at launch."""
    source = (REPO / "src" / "json2gcs" / "__main__.py").read_text(encoding="utf-8")
    assert "from json2gcs.cli import main" in source
    # Prose may mention the trap; the code must not contain it.
    imports = [line for line in source.splitlines() if line.startswith(("from ", "import "))]
    assert not [line for line in imports if line.startswith("from .")], imports
