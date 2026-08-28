"""Build a GCS sheet from a Foundry export alone — mode B of docs/01-problem.md.

For actors that were never in GCS: GCA imports, hand-built NPCs, or a character
whose original ``.gcs`` is simply lost.  Honestly lower fidelity than merge
mode, because everything Foundry never knew about — modifiers, features,
prereqs, difficulty, tags, library ``source`` links — is not in the export to
recover.  What comes out is structurally valid and finishable in GCS.

**This is merge mode against an empty sheet**, not a second implementation.
The template in ``data/default.gcs`` is what GCS itself produces when handed a
stub file, so the default attributes, body plan and page settings are the
application's own rather than a transcription of them (and
``test_the_template_is_what_gcs_itself_produces`` keeps that true).  Every row
in the export is then simply ADDED, and the existing reconciler and writer do
the work.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import apply as applymod
from . import foundry, gcs, jsonio, reconcile
from . import tid as tidmod

__all__ = ["TEMPLATE", "blank_sheet", "synthesize"]

TEMPLATE = Path(__file__).resolve().parent / "data" / "default.gcs"


def blank_sheet(*, now: datetime | None = None) -> gcs.Sheet:
    """A fresh, empty GCS sheet with GCS's own defaults.

    The template carries one fixed entity id and the zero timestamp GCS writes
    when it has nothing better; both are replaced, so two sheets synthesized
    from different actors are not two copies of the same character.
    """
    sheet = gcs.loads(jsonio.read_text(TEMPLATE))
    sheet.data["id"] = tidmod.mint(tidmod.Kind.ENTITY)
    stamp = (now or datetime.now().astimezone()).replace(microsecond=0).isoformat()
    sheet.data["created_date"] = stamp
    sheet.data["modified_date"] = stamp
    return sheet


def synthesize(
    actor: foundry.Actor,
    *,
    include_lossy: bool = False,
    now: datetime | None = None,
) -> tuple[gcs.Sheet, reconcile.Reconciliation, applymod.Plan]:
    """Turn an export into a sheet. Returns the sheet and the usual paperwork.

    ``rename`` is on, unlike merge mode: there is no existing sheet name to
    protect, and a sheet called "" would be worse than one named for the actor.
    """
    sheet = blank_sheet(now=now)
    result = reconcile.reconcile(actor, sheet, rename=True)
    plan = applymod.apply(result, sheet, include_lossy=include_lossy, now=now)
    return sheet, result, plan
