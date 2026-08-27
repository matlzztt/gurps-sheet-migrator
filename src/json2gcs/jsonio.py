"""Order- and format-preserving JSON I/O for GCS files.

GCS writes its files with a very specific shape (docs/02-gcs-format.md 2.1):
tab indent, LF endings, raw UTF-8, one trailing newline, and Go struct field
order rather than alphabetical.  Python's :func:`json.dump` cannot reproduce any
of that, so this module implements the reader and writer directly.

The design goal is that ``dumps(loads(text)) == text`` for any file GCS wrote.
That property is what lets us treat GCS's own serializer as the test oracle
(docs/06-architecture.md 6.5): once round-tripping is byte-exact, any diff after
a conversion is a real change we made, never serializer noise.

Two things make byte-exactness achievable:

* **Numbers are kept verbatim.**  GCS uses fixed-point arithmetic (``fxp.Int``),
  so ``0.25`` must come back as ``0.25`` and never as ``0.25000000000000001``.
  :class:`Num` carries the original literal text and re-emits it unchanged,
  while exposing a :class:`~decimal.Decimal` for code that needs the value.
* **Object key order is preserved.**  ``dict`` is insertion-ordered, so simply
  not sorting is enough for round-tripping.  Writing *new* keys in the right
  place is a separate problem, handled by the field-order tables elsewhere.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

__all__ = ["Num", "loads", "dumps", "load", "dump", "read_text", "write_text"]


class Num:
    """A JSON number that remembers exactly how it was written.

    Compares and hashes by numeric value, so ``Num("6") == Num("6.0")`` is
    ``True`` and both equal ``6``.  Serializes back as :attr:`raw`.
    """

    __slots__ = ("raw", "value")

    def __init__(self, raw: str | int | float | Decimal):
        if isinstance(raw, str):
            self.raw = raw
            self.value = Decimal(raw)
        else:
            self.value = Decimal(str(raw))
            self.raw = format_number(self.value)

    def __repr__(self) -> str:
        return f"Num({self.raw!r})"

    def __str__(self) -> str:
        return self.raw

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Num):
            return self.value == other.value
        if isinstance(other, (int, Decimal)):
            return self.value == other
        if isinstance(other, float):
            return self.value == Decimal(str(other))
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __bool__(self) -> bool:
        return self.value != 0

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)


def format_number(value: Decimal | int | float | str) -> str:
    """Render a number the way GCS does: no exponent, no trailing zeros.

    GCS writes ``0.25``, ``6`` and ``-2`` — never ``6.0``, ``2.5E-1`` or
    ``0.250000``.  Going through :class:`~decimal.Decimal` rather than
    ``repr(float)`` keeps fixed-point values exact.
    """
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if d == d.to_integral_value():
        # normalize() would turn 100 into 1E+2, so take the integral path.
        return str(d.quantize(Decimal(1)))
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

_DECODER = json.JSONDecoder(parse_float=Num, parse_int=Num)


def loads(text: str) -> Any:
    """Parse GCS JSON, preserving key order and number literals."""
    return _DECODER.decode(text)


def read_text(path: str | Path) -> str:
    """Read a GCS file as text, stripping a UTF-8 BOM if present.

    ``jio.LoadFromFile`` runs the bytes through a BOM stripper, so a BOM is
    tolerated on input even though GCS never writes one.
    """
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8")


def load(path: str | Path) -> Any:
    """Read and parse a GCS file."""
    return loads(read_text(path))


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

# GCS escapes only what JSON requires. It does *not* HTML-escape: '<', '>' and
# '&' appear raw in real files (verified against samples/sturm/sturm.gcs), which
# matches encoding/json/v2's default and differs from Go's v1 encoder.
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _encode_string(s: str) -> str:
    out = ['"']
    for ch in s:
        esc = _ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ch < " ":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _write(value: Any, out: list[str], depth: int) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, Num):
        out.append(value.raw)
    elif isinstance(value, str):
        out.append(_encode_string(value))
    elif isinstance(value, (int, Decimal, float)):
        out.append(format_number(value))
    elif isinstance(value, dict):
        _write_object(value, out, depth)
    elif isinstance(value, (list, tuple)):
        _write_array(value, out, depth)
    else:
        raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_object(obj: dict, out: list[str], depth: int) -> None:
    if not obj:
        out.append("{}")
        return
    inner = "\t" * (depth + 1)
    out.append("{\n")
    for i, (key, val) in enumerate(obj.items()):
        if i:
            out.append(",\n")
        out.append(inner)
        out.append(_encode_string(str(key)))
        out.append(": ")
        _write(val, out, depth + 1)
    out.append("\n")
    out.append("\t" * depth)
    out.append("}")


def _write_array(seq, out: list[str], depth: int) -> None:
    if not seq:
        out.append("[]")
        return
    inner = "\t" * (depth + 1)
    out.append("[\n")
    for i, val in enumerate(seq):
        if i:
            out.append(",\n")
        out.append(inner)
        _write(val, out, depth + 1)
    out.append("\n")
    out.append("\t" * depth)
    out.append("]")


def dumps(value: Any) -> str:
    """Serialize to GCS's exact on-disk format, including the trailing newline."""
    out: list[str] = []
    _write(value, out, 0)
    out.append("\n")
    return "".join(out)


def write_text(path: str | Path, text: str) -> None:
    """Write text as UTF-8 with LF endings and no BOM.

    ``newline=""`` is essential: on Windows the default would translate every
    LF to CRLF and break the format contract.
    """
    Path(path).write_bytes(text.encode("utf-8"))


def dump(path: str | Path, value: Any) -> None:
    """Serialize and write a GCS file."""
    write_text(path, dumps(value))
