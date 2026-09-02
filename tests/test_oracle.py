"""Use the real GCS application as the test oracle.

`gcs --convert` loads a file, rewrites it in the current data format and exits,
so the application itself can tell us whether our output is right
(docs/06-architecture.md 6.5).  Nothing else can: our own reader agreeing with
our own writer proves only that they are consistent with each other.

These tests skip when GCS is not installed.  Point them at it with
``JSON2GCS_GCS=/path/to/gcs`` if it is not on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from json2gcs import apply, cli, foundry, gcs, jsonio, reconcile, synthesize

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"

BINARY = cli.find_gcs()
needs_gcs = pytest.mark.skipif(
    BINARY is None, reason="GCS not installed; set JSON2GCS_GCS to enable"
)

CHARACTERS = REPO / "samples" / "characters"

FIXTURES = [
    REPO / "samples" / "sturm" / "sturm.gcs",
    DIR / "container.gcs",
    REPO / "samples" / "upstream" / "issue767.gcs",
    CHARACTERS / "Alys Dustin.gcs",
    CHARACTERS / "Sharpbend.gcs",
    CHARACTERS / "Surubash.gcs",
    CHARACTERS / "Suruchin.gcs",
]


def _gcs_rewrite(source: Path, workdir: Path) -> bytes:
    """Copy a file, run it through GCS, and return what GCS wrote."""
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / source.name
    shutil.copy(source, target)
    ok, detail = cli.run_gcs_convert(BINARY, target)
    assert ok, f"gcs --convert failed on {source.name}: {detail}"
    return target.read_bytes()


@needs_gcs
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_gcs_rewrites_the_fixtures_unchanged_apart_from_calc(path: Path, tmp_path: Path):
    """The fixtures are a fixed point of GCS's own serializer.

    Worth asserting on its own: it means byte-comparing against them is a
    meaningful test rather than a comparison against an arbitrary encoding.

    `calc` is excluded because `issue767.gcs` is upstream's regression fixture
    for a damage-calculation bug — it records `1d-2 cr` where current GCS
    computes `1d-1 cr`. Being write-only, that difference is inert.
    """
    rewritten = jsonio.loads(_gcs_rewrite(path, tmp_path).decode("utf-8"))
    original = jsonio.loads(jsonio.read_text(path))
    assert jsonio.dumps(cli._strip_calc(rewritten)) == jsonio.dumps(
        cli._strip_calc(original)
    )


@needs_gcs
@pytest.mark.parametrize(
    "path",
    [
        REPO / "samples" / "sturm" / "sturm.gcs",
        DIR / "container.gcs",
        CHARACTERS / "Alys Dustin.gcs",
        CHARACTERS / "Sharpbend.gcs",
        CHARACTERS / "Surubash.gcs",
        CHARACTERS / "Suruchin.gcs",
    ],
    ids=lambda p: p.name,
)
def test_our_own_fixtures_are_exact_fixed_points(path: Path, tmp_path: Path):
    """Byte-for-byte, for the sheets we captured ourselves."""
    assert _gcs_rewrite(path, tmp_path) == path.read_bytes()


@needs_gcs
def test_gcs_accepts_our_merged_output_and_changes_only_calc(tmp_path: Path):
    """The strongest test available: GCS agrees with our writer everywhere.

    `calc` is expected to differ — GCS recomputes it from the edits we made,
    which is exactly why it is write-only (docs/02-gcs-format.md 2.6).
    """
    sheet = gcs.load(DIR / "container.gcs")
    result = reconcile.reconcile(
        foundry.load(DIR / "container-played.foundry.json"), sheet
    )
    apply.apply(result, sheet)

    ours = tmp_path / "merged.gcs"
    jsonio.dump(ours, sheet.data)
    theirs = jsonio.loads(_gcs_rewrite(ours, tmp_path / "sub").decode("utf-8"))

    mine = jsonio.loads(jsonio.read_text(ours))
    assert jsonio.dumps(cli._strip_calc(mine)) == jsonio.dumps(cli._strip_calc(theirs))


@needs_gcs
def test_gcs_recomputes_our_edits_correctly(tmp_path: Path):
    """GCS's own numbers confirm the edits landed as intended."""
    sheet = gcs.load(DIR / "container.gcs")
    result = reconcile.reconcile(
        foundry.load(DIR / "container-played.foundry.json"), sheet
    )
    apply.apply(result, sheet)
    ours = tmp_path / "merged.gcs"
    jsonio.dump(ours, sheet.data)
    cli.run_gcs_convert(BINARY, ours)

    rewritten = gcs.load(ours)
    attrs = {a["attr_id"]: a for a in rewritten.data["attributes"]}
    # We wrote damage; GCS derived the current pool from it.
    assert str(attrs["hp"]["calc"]["current"]) == "6"   # 10 - 4
    assert str(attrs["fp"]["calc"]["current"]) == "3"   # 11 - 8

    # We wrote quantity 4; GCS extended value and weight from it.
    arrow = rewritten.by_tid["eQvR7mN2xLkT4bH9c"]["calc"] if False else (
        rewritten.by_tid["eQvR7mN2xLkT4bH9c"].data["calc"]
    )
    assert str(arrow["extended_value"]) == "8"          # 2 x 4
    assert arrow["extended_weight"] == "0.4 lb"         # 0.1 lb x 4


@needs_gcs
def test_refresh_calc_makes_the_output_a_fixed_point(tmp_path: Path):
    """After --refresh-calc, GCS rewrites the file to itself, byte for byte."""
    out = tmp_path / "merged.gcs"
    code = cli.main(
        [
            "convert",
            str(DIR / "container-played.foundry.json"),
            "--base",
            str(DIR / "container.gcs"),
            "-o",
            str(out),
            "--refresh-calc",
        ]
    )
    assert code == 0
    assert _gcs_rewrite(out, tmp_path / "sub") == out.read_bytes()


@needs_gcs
def test_a_row_added_in_foundry_survives_gcs(tmp_path: Path):
    """A minted TID and a sparse synthesized row must still load."""
    import json

    payload = json.loads((DIR / "container-played.foundry.json").read_text("utf-8"))
    payload["system"]["skills"]["09999"] = {
        "name": "Riding",
        "points": "2",
        "pageref": "B217",
        "save": True,
    }
    sheet = gcs.load(DIR / "container.gcs")
    result = reconcile.reconcile(foundry.loads(json.dumps(payload)), sheet)
    plan = apply.apply(result, sheet)
    minted = plan.added[0][1]

    out = tmp_path / "merged.gcs"
    jsonio.dump(out, sheet.data)
    ok, detail = cli.run_gcs_convert(BINARY, out)
    assert ok, detail
    assert minted in gcs.load(out).by_tid


def test_find_gcs_honours_an_explicit_path(tmp_path: Path):
    fake = tmp_path / "gcs.exe"
    fake.write_text("", encoding="utf-8")
    assert cli.find_gcs(str(fake)) == fake
    assert cli.find_gcs(str(tmp_path / "nope.exe")) is None


@needs_gcs
def test_gcs_accepts_a_sheet_whose_rows_moved(tmp_path: Path):
    """A relocated row is still a row GCS is happy to load and rewrite.

    Re-parenting is the one edit that changes the sheet's shape rather than a
    value in it, so it is the one most likely to produce something that parses
    here and not there.
    """
    from test_moves import BACKPACK, YARQAP, _moved  # noqa: PLC0415

    sheet = gcs.load(DIR / "container.gcs")
    actor = _moved(tid=YARQAP, frm=["carried"], to=["carried", BACKPACK])
    plan = apply.apply(reconcile.reconcile(actor, sheet), sheet)
    assert len(plan.moved) == 1

    ours = tmp_path / "moved.gcs"
    jsonio.dump(ours, sheet.data)
    theirs = jsonio.loads(_gcs_rewrite(ours, tmp_path / "sub").decode("utf-8"))

    mine = jsonio.loads(jsonio.read_text(ours))
    assert jsonio.dumps(cli._strip_calc(mine)) == jsonio.dumps(cli._strip_calc(theirs))

    # And GCS kept it where we put it, rather than hoisting it back out.
    backpack = gcs.load(ours).by_tid[BACKPACK]
    assert YARQAP in [child["id"] for child in backpack.data["children"]]


@needs_gcs
def test_the_template_is_what_gcs_itself_produces(tmp_path: Path):
    """The synthesize template must stay GCS's defaults, not a transcription.

    Hand GCS a stub with nothing but a version and it fills in the whole
    default sheet -- attributes, body plan, page settings. That output *is*
    ``data/default.gcs``. If GCS's defaults change, this fails, and the fix is
    to recapture the template rather than to edit it.
    """
    stub = tmp_path / "stub.gcs"
    stub.write_text('{"version":5}', encoding="utf-8", newline="")
    ok, detail = cli.run_gcs_convert(BINARY, stub)
    assert ok, detail

    produced = jsonio.loads(stub.read_text(encoding="utf-8"))
    template = jsonio.loads(jsonio.read_text(synthesize.TEMPLATE))
    # The entity id is minted per file, and blank_sheet replaces it anyway.
    produced.pop("id"), template.pop("id")
    assert jsonio.dumps(produced) == jsonio.dumps(template)


@needs_gcs
def test_gcs_accepts_a_synthesized_sheet(tmp_path: Path):
    """Mode B's whole claim is 'structurally valid'. This is what tests it."""
    sheet, _, _ = synthesize.synthesize(foundry.load(DIR / "container.foundry.json"))
    ours = tmp_path / "new.gcs"
    jsonio.dump(ours, sheet.data)

    theirs = jsonio.loads(_gcs_rewrite(ours, tmp_path / "sub").decode("utf-8"))
    mine = jsonio.loads(jsonio.read_text(ours))

    # GCS adds a points_record entry reconciling the total it computes against
    # the one the export reported -- it has never seen this character before.
    # That is GCS doing its job, and it is the only structural difference.
    assert "points_record" not in mine
    assert [r["reason"] for r in theirs["points_record"]] == ["Reconciliation"]
    del theirs["points_record"]

    assert jsonio.dumps(cli._strip_calc(mine)) == jsonio.dumps(
        cli._strip_calc(theirs)
    ), "GCS disagrees with something we wrote"


@needs_gcs
def test_a_synthesized_sheet_settles_after_one_pass(tmp_path: Path):
    """Once GCS has recorded its reconciliation, the file is a fixed point."""
    out = tmp_path / "new.gcs"
    code = cli.main(
        [
            "convert",
            str(DIR / "container.foundry.json"),
            "--synthesize",
            "-o",
            str(out),
            "--refresh-calc",
        ]
    )
    assert code == 0
    assert _gcs_rewrite(out, tmp_path / "sub") == out.read_bytes()


@needs_gcs
def test_a_synthesized_sheet_can_be_merged_into_later(tmp_path: Path):
    """Rows keep their TIDs, so the sheet mode B produces is a usable base:
    the same export reconciles against it with nothing left to carry back."""
    sheet, _, _ = synthesize.synthesize(foundry.load(DIR / "container.foundry.json"))
    out = tmp_path / "new.gcs"
    jsonio.dump(out, sheet.data)
    cli.run_gcs_convert(BINARY, out)

    again = reconcile.reconcile(
        foundry.load(DIR / "container.foundry.json"), gcs.load(out)
    )
    assert again.by_status(reconcile.Status.ADDED) == []
    assert again.by_status(reconcile.Status.MISSING) == []
    assert [d.name for d in again.deltas if d.moved] == []
