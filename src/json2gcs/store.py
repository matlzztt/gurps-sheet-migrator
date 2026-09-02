"""The snapshot store — the sheet as it was when Foundry imported from it.

Merging with ``--base`` is a **two-way** comparison, and it quietly assumes the
sheet is still what Foundry read.  When it is not, the failure is invisible:
every field the player did not touch reads as "unchanged" against a stale
export, so an edit made in GCS after the export is reverted by a value nobody
typed.  A two-way merge cannot tell that apart from a real match — the missing
piece is the **common ancestor** (docs/06-architecture.md 6.9).

This module keeps one.  Snapshots are stored as the sheet's original bytes,
keyed by content hash:

* **Bytes, not an extraction.** The byte stream is the contract everywhere else
  in this project (docs/06-architecture.md 6.5), a copy cannot drift from the
  schema, and a whole sheet is 20-100 KB.
* **Content hash, not the entity id.** Three of the sample characters carry an
  entity id GCS itself rejects and remints (docs/08-improvements.md 8.4), so the
  id is not a key that can be relied on. A hash also dedupes re-remembering an
  unchanged file for free.

Lookup is by **row TID**, which is an identity rather than a name, so finding
the sheet an export came from is deduction and not a guess.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import foundry, gcs

__all__ = ["Snapshot", "Store", "default_root", "find_root"]

#: Bumped only if the on-disk layout stops being readable by an older build.
INDEX_VERSION = 1


def default_root() -> Path:
    """Where snapshots live when nobody says otherwise."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "json2gcs" / "store"


def find_root(explicit: str | os.PathLike | None = None) -> Path:
    """Resolve the store location: argument, then env var, then the default.

    The same order :func:`json2gcs.cli.find_gcs` uses, so there is one rule to
    remember for both.
    """
    for candidate in (explicit, os.environ.get("JSON2GCS_STORE")):
        if candidate:
            return Path(candidate)
    return default_root()


@dataclass(frozen=True)
class Snapshot:
    """One remembered sheet."""

    digest: str
    """Content hash of the original bytes; also the filename."""

    name: str
    entity_id: str
    source: str
    """Where it was read from, for the reader's benefit only — never a key."""

    modified_date: str
    """The sheet's own ``modified_date``, which is what dates the *content*."""

    remembered: str
    rows: int
    tids: list[str] = field(default_factory=list)

    @property
    def written(self) -> datetime | None:
        """``modified_date`` as a naive local time, to compare against an import."""
        if not self.modified_date:
            return None
        try:
            return datetime.fromisoformat(self.modified_date).replace(tzinfo=None)
        except ValueError:
            return None

    def describe(self) -> str:
        return (
            f"{self.name or '(unnamed)'}  {self.rows} rows  "
            f"written {self.modified_date or '?'}  [{self.digest}]"
        )


class Store:
    """A directory of remembered sheets, plus a TID index into them."""

    def __init__(self, root: str | os.PathLike | None = None) -> None:
        self.root = Path(root) if root is not None else find_root()

    # ---- layout ---------------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self.root / "index.json"

    def blob_path(self, digest: str) -> Path:
        """Where a snapshot's bytes live. A real file, readable by anything."""
        return self.root / "sheets" / f"{digest}.gcs"

    def _read_index(self) -> dict:
        try:
            raw = self._index_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": INDEX_VERSION, "snapshots": {}}
        try:
            data = json.loads(raw)
        except ValueError:
            # A corrupt index must not cost the user their snapshots: the blobs
            # are the real data and the index is derivable from them.
            return {"version": INDEX_VERSION, "snapshots": {}}
        data.setdefault("snapshots", {})
        return data

    def _write_index(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # Write beside the target and replace, so an interrupted write cannot
        # leave a half-parsed index behind.
        temporary = self._index_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, self._index_path)

    # ---- writing --------------------------------------------------------

    def remember(self, path: str | os.PathLike, *, now: datetime | None = None) -> tuple[Snapshot, bool]:
        """Store a copy of a sheet. Returns ``(snapshot, is_new)``.

        Re-remembering an unchanged file is a no-op that returns the snapshot
        already held, because the hash is the identity.
        """
        source = Path(path)
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()[:16]

        index = self._read_index()
        if digest in index["snapshots"]:
            return self._snapshot(digest, index["snapshots"][digest]), False

        sheet = gcs.load(source)
        # Sub-second precision on purpose: two snapshots taken in the same run
        # (one by `remember`, one by `convert`) would otherwise tie, and
        # `ancestor_for` has to break that tie the same way every time.
        stamp = now or datetime.now().astimezone()
        record = {
            "name": sheet.profile.get("name", "") or source.stem,
            "entity_id": str(sheet.data.get("id", "")),
            "source": str(source.resolve()),
            "modified_date": str(sheet.data.get("modified_date", "")),
            "remembered": stamp.isoformat(),
            "rows": len(sheet.by_tid),
            "tids": sorted(sheet.by_tid),
        }

        blob = self.blob_path(digest)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(raw)

        index["snapshots"][digest] = record
        index["version"] = INDEX_VERSION
        self._write_index(index)
        return self._snapshot(digest, record), True

    # ---- reading --------------------------------------------------------

    @staticmethod
    def _snapshot(digest: str, record: dict) -> Snapshot:
        return Snapshot(
            digest=digest,
            name=record.get("name", ""),
            entity_id=record.get("entity_id", ""),
            source=record.get("source", ""),
            modified_date=record.get("modified_date", ""),
            remembered=record.get("remembered", ""),
            rows=int(record.get("rows", 0)),
            tids=list(record.get("tids") or ()),
        )

    def snapshots(self) -> list[Snapshot]:
        """Everything remembered, newest content first."""
        index = self._read_index()
        found = [self._snapshot(d, r) for d, r in index["snapshots"].items()]
        return sorted(found, key=lambda s: (s.modified_date, s.remembered), reverse=True)

    def bytes_of(self, digest: str) -> bytes:
        return self.blob_path(digest).read_bytes()

    def matches(self, actor: foundry.Actor) -> list[tuple[Snapshot, int]]:
        """Snapshots sharing row TIDs with this export, most overlap first.

        A TID is an identity, so any overlap at all means the same character;
        the count ranks several remembered states of it against each other.
        """
        wanted = {row.tid for row in actor.rows() if row.tid}
        if not wanted:
            return []
        scored = []
        for snapshot in self.snapshots():
            shared = len(wanted.intersection(snapshot.tids))
            if shared:
                scored.append((snapshot, shared))
        scored.sort(key=lambda pair: (pair[1], pair[0].modified_date), reverse=True)
        return scored

    def ancestor_for(self, actor: foundry.Actor) -> Snapshot | None:
        """The remembered sheet this export was imported from, if we have it.

        Among the snapshots of this character, the ancestor is the newest one
        **not written after** Foundry imported — that is the state Foundry
        actually read.  Falling back to the best TID match when the timestamps
        cannot decide keeps this useful on exports GGA stamped oddly; the
        caller is told which of the two it got.

        Two snapshots can share a ``modified_date`` — most often because
        ``convert`` re-remembers its base sheet, so a run stores a second copy
        alongside one already held.  Ties break towards the one remembered
        *first*: a later snapshot carrying the same content date is usually the
        current file being re-recorded, not an older state.  Deterministic
        either way, which matters more than which rule is chosen.
        """
        candidates = self.matches(actor)
        if not candidates:
            return None
        imported = foundry.parse_last_import(actor.last_import)
        if imported is not None:
            best = max(shared for _, shared in candidates)
            eligible = [
                snapshot
                for snapshot, shared in candidates
                if shared == best
                and snapshot.written is not None
                and snapshot.written <= imported
            ]
            if eligible:
                newest = max(s.written for s in eligible)
                tied = [s for s in eligible if s.written == newest]
                return min(tied, key=lambda s: s.remembered)
        return candidates[0][0]
