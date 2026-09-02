"""Render a :class:`~json2gcs.reconcile.Reconciliation` as readable text.

The report is the product, not a debug dump: someone finishing a session should
be able to read it and recognise what they did.  Three things follow from that:

* Changes that can be applied are separated from ones that need a human.
* A cascade is shown indented under the action that caused it, not as four
  unexplained edits (docs/05-fidelity.md 5.7).
* Rows missing from the export are called ambiguous, because they are.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from .fields import Fidelity
from .jsonio import Num
from .reconcile import Change, Reconciliation, RowDelta, Status

__all__ = ["render"]

_SECTION_LABEL = {
    "traits": "traits",
    "skills": "skills",
    "spells": "spells",
    "equipment": "equipment (carried)",
    "other_equipment": "equipment (other)",
    "notes": "notes",
}


def _show(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Num):
        return value.raw
    text = str(value)
    if len(text) > 48:
        text = text[:45].rstrip() + "…"
    return f'"{text}"' if isinstance(value, str) else text


#: Fields that cascade from a container to its contents.
_CASCADING = {"equipped"}


def _text_delta(old, new) -> str | None:
    """Describe a long text edit usefully instead of showing two ellipses.

    Compares on collapsed whitespace, because GGA re-indents notes on every
    save (docs/05-fidelity.md 5.7); without that, a simple append never looks
    like one and the reader is told only that the text "differs".
    """
    old_text, new_text = str(old or ""), str(new or "")
    if len(old_text) < 60 and len(new_text) < 60:
        return None
    flat_old, flat_new = " ".join(old_text.split()), " ".join(new_text.split())
    if flat_new.startswith(flat_old):
        return f"appended {_quote(flat_new[len(flat_old):])}"
    if flat_old.startswith(flat_new):
        return f"removed {_quote(flat_old[len(flat_new):])}"
    return f"text differs ({len(old_text)} → {len(new_text)} chars)"


def _quote(fragment: str) -> str:
    fragment = " ".join(fragment.split())
    if len(fragment) > 40:
        fragment = fragment[:37].rstrip() + "…"
    return f'"{fragment}"'


def _change_line(change: Change, indent: str) -> str:
    summary = _text_delta(change.old, change.new)
    if summary:
        return f"{indent}{change.label:<12} {summary}"
    return f"{indent}{change.label:<12} {_show(change.old)} → {_show(change.new)}"


def _group(deltas: Iterable[RowDelta]) -> Iterator[tuple[str, list[RowDelta]]]:
    current: str | None = None
    batch: list[RowDelta] = []
    for delta in deltas:
        if delta.section != current:
            if batch:
                yield current, batch
            current, batch = delta.section, []
        batch.append(delta)
    if batch:
        yield current, batch


def render(result: Reconciliation, *, verbose: bool = False) -> str:
    out: list[str] = []
    summary = result.summary()

    applicable = [
        (d, [c for c in d.changes if c.applicable])
        for d in result.changed_rows
    ]
    applicable = [(d, cs) for d, cs in applicable if cs]

    review = [(d, [c for c in d.changes if not c.applicable]) for d in result.changed_rows]
    review = [(d, cs) for d, cs in review if cs]

    sheet_changes = result.profile + result.attributes + result.points

    # ---- changes we could apply -------------------------------------------
    if applicable or sheet_changes:
        out.append("Changes to carry back")
        for section, deltas in _group(d for d, _ in applicable):
            out.append(f"  {_SECTION_LABEL.get(section, section)}")
            by_tid = {d.tid: cs for d, cs in applicable}
            for delta in deltas:
                marker = "  └ " if delta.cascade_from else "    "
                out.append(f"  {marker}{delta.name}")
                for change in by_tid[delta.tid]:
                    line = _change_line(change, "        ")
                    # The cascade explains the flag that cascaded, not every
                    # edit on the row: a rename that happens to sit inside an
                    # un-equipped container is still a rename.
                    if delta.cascade_from and change.field in _CASCADING:
                        line += "   (follows its container)"
                    out.append(line)
        if sheet_changes:
            out.append("  character")
            for change in sheet_changes:
                out.append(_change_line(change, "        "))
        out.append("")

    # ---- rows only on one side --------------------------------------------
    added = result.by_status(Status.ADDED)
    if added:
        out.append(f"Added in Foundry ({len(added)}) — will need new GCS ids")
        for delta in added:
            out.append(f"    {_SECTION_LABEL.get(delta.section, delta.section)}: {delta.name}")
        out.append("")

    missing = result.by_status(Status.MISSING)
    if missing:
        out.append(f"In the sheet but not the export ({len(missing)}) — ambiguous")
        for delta in missing:
            out.append(f"    {_SECTION_LABEL.get(delta.section, delta.section)}: {delta.name}")
        out.append(
            "    Either deleted in Foundry or added to the sheet after the export."
        )
        out.append("    Nothing in either file tells them apart, so these are kept.")
        out.append("")

    moved = [d for d in result.deltas if d.moved]
    if moved:
        out.append(f"Moved ({len(moved)})")
        for delta in moved:
            out.append(
                f"    {delta.name}: {delta.moved_from_label} → "
                f"{delta.moved_to_label}"
            )
        out.append("")

    # ---- things a human has to decide -------------------------------------
    if review:
        total = sum(len(cs) for _, cs in review)
        out.append(f"Needs review ({total}) — found, but not safe to apply")
        for delta, changes in review:
            out.append(f"    {delta.name}")
            for change in changes:
                out.append(_change_line(change, "        "))
                reason = change.blocked or (
                    "lossy: Foundry's value is a rendering, not the input"
                    if change.fidelity is Fidelity.LOSSY
                    else ""
                )
                if reason:
                    out.append(f"            {reason}")
        out.append("")

    # ---- what the three-way comparison spared ------------------------------
    # Only ever non-empty with an ancestor to compare against: these are the
    # fields a two-way merge would have quietly reverted.
    superseded = result.superseded
    if superseded:
        out.append(
            f"Already newer in the sheet ({len(superseded)}) — left alone"
        )
        for delta, change in superseded:
            out.append(f"    {delta.name}")
            out.append(
                f"        {change.label:<12} keeping {_show(change.old)}; "
                f"the export still has {_show(change.new)} from before the import"
            )
        out.append("")

    if result.warnings:
        out.append("Warnings")
        for warning in result.warnings:
            out.append(f"  ! {warning}")
        out.append("")

    if result.is_empty:
        out.append("No differences. The sheet already matches the export.")
        out.append("")

    out.append(
        "  ".join(
            f"{key} {value}"
            for key, value in summary.items()
            if value or key in ("matched", "changed")
        )
    )
    if verbose:
        out.append("")
        out.append("Unchanged rows:")
        for delta in result.deltas:
            if delta.status is Status.MATCHED and not delta.interesting:
                out.append(f"    {delta.section}: {delta.name}")
    return "\n".join(out)
