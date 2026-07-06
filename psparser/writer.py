"""Serialize decoded Parasolid nodes back to XT binary (inverse of the reader).

Mirrors reader.py: `Writer` is the byte-emitting dual of `Reader` (one method per
XT primitive, encoding the same null sentinels the reader decodes to `None` --
pointer -> 1, float64 -> the -3.14158e13 sentinel), and `WRITERS` is the
FieldType -> encode dispatch table dual of `READERS`.  `FieldDef.write` uses it,
exactly as `FieldDef.read` uses `READERS`.

`write_document(doc)` walks a `Document` (from `read_document`): it re-emits the
captured header verbatim, each node type's schema blob once at first occurrence,
every node's type/count/id/fields, and the captured terminator.  For an
unmodified document this reproduces the input byte for byte; mutate `doc.nodes`
(or swap `doc.header`) to write a changed file.
"""

import struct
from typing import TYPE_CHECKING, Callable

from .reader import F64_NULL, FieldType

if TYPE_CHECKING:
    from .parser import Document

PTR_NULL = 1  # pointer wire value meaning "null"


class Writer:
    """Byte-emitting dual of reader.Reader for XT primitive field types."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def u8(self, value: int) -> None:
        self.buf += struct.pack(">B", value)

    def i16(self, value: int) -> None:
        self.buf += int(value).to_bytes(2, "big", signed=True)

    def i32(self, value: int) -> None:
        self.buf += int(value).to_bytes(4, "big", signed=True)

    def f64(self, value: float | None) -> None:
        self.buf += F64_NULL if value is None else struct.pack(">d", value)

    def char(self, text: str) -> None:
        """Write ASCII text (fixed-length string field; no length prefix)."""
        self.buf += text.encode("ascii")

    def bool8(self, value: bool) -> None:
        self.buf += b"\x01" if value else b"\x00"

    def pointer(self, value: int | None) -> None:
        self.i16(PTR_NULL if value is None else value)

    def utf16_be(self, text: str) -> None:
        self.buf += text.encode("utf-16-be")

    def interval(self, value) -> None:
        self.f64(value[0])
        self.f64(value[1])

    def vector(self, value) -> None:
        for component in value:
            self.f64(component)

    def box(self, value) -> None:
        for iv in value:
            self.interval(iv)

    def str_u8_len(self, text: str) -> None:
        encoded = text.encode("ascii")
        self.u8(len(encoded))
        self.buf += encoded

    def str_i32_len(self, text: str) -> None:
        encoded = text.encode("ascii")
        self.i32(len(encoded))
        self.buf += encoded


# FieldType -> encode function; the write-side dual of reader.READERS.
WRITERS: dict[FieldType, Callable[[Writer, object], None]] = {
    FieldType.U8: lambda w, v: w.u8(v),
    FieldType.CHAR: lambda w, v: w.char(v),
    FieldType.LOGICAL: lambda w, v: w.bool8(v),
    FieldType.I16: lambda w, v: w.i16(v),
    FieldType.UTF16: lambda w, v: w.utf16_be(v),
    FieldType.I32: lambda w, v: w.i32(v),
    FieldType.POINTER: lambda w, v: w.pointer(v),
    FieldType.F64: lambda w, v: w.f64(v),
    FieldType.INTERVAL: lambda w, v: w.interval(v),
    FieldType.VECTOR: lambda w, v: w.vector(v),
    FieldType.BOX: lambda w, v: w.box(v),
    FieldType.H: lambda w, v: w.vector(v),
}


def write_document(doc: "Document") -> bytes:
    """Serialize a Document to XT binary bytes (inverse of read_document)."""
    writer = Writer()
    writer.buf += doc.header
    emitted_schema: set[int] = set()

    for node in doc.nodes:
        node_type = node["node_type"]
        writer.i16(node_type)

        if node_type not in emitted_schema:
            try:
                writer.buf += doc.schema_blobs[node_type]
            except KeyError as exc:
                raise KeyError(
                    f"no schema blob for node type {node_type}; the Document must "
                    "carry an embedded-schema blob for every node type it writes"
                ) from exc
            emitted_schema.add(node_type)

        fields = doc.layouts[node_type]
        variable = doc.variable[node_type]
        if variable:
            writer.i32(node["count"])
        writer.i16(node["id"])

        last = len(fields) - 1
        for idx, fdef in enumerate(fields):
            count = node["count"] if (variable and idx == last) else 1
            fdef.write(writer, node[fdef.name], count)

    writer.buf += doc.terminator
    return bytes(writer.buf)
