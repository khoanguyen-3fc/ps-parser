"""Dynamic, schema-aware decoder for Parasolid XT binary part files."""

import io
import logging
from dataclasses import dataclass
from typing import BinaryIO

from .reader import Reader
from .schema import (
    FieldDef,
    Schema,
    TypeDef,
    parse_embedded_field,
    to_field_def,
)

logger = logging.getLogger(__name__)


def parse_file_header(reader: Reader) -> tuple[str, str, int, int]:
    """Parse optional text header and required PS binary header."""
    prefix = reader.stream.read(2)

    if prefix == b"**":
        reader.stream.seek(0)
        header_window = reader.stream.read(1024)
        marker = b"**END_OF_HEADER**"
        marker_pos = header_window.find(marker)
        if marker_pos == -1:
            raise ValueError("Invalid file: missing **END_OF_HEADER** marker")

        newline_pos = header_window.find(b"\n", marker_pos + len(marker))
        if newline_pos == -1:
            raise ValueError("Invalid file: missing newline after header marker")

        reader.stream.seek(newline_pos + 1)
        prefix = reader.stream.read(2)

    if prefix != b"PS":
        raise ValueError("Invalid file: only PS binary format is supported")

    modeler_version = reader.str_i32_len()
    schema_name = reader.str_i32_len()
    schema_max_type = reader.i16()
    schema_min_type = reader.i16()

    reader.stream.read(2)  # Unknown bytes currently not interpreted.

    return modeler_version, schema_name, schema_min_type, schema_max_type


def resolve_node_schema(
    reader: Reader,
    node_type: int,
    field_count: int,
    base_schema: Schema,
) -> list[FieldDef]:
    """Resolve node fields from embedded schema data.

    Handles both delta-schema and full-schema payloads. Updates `base_schema` when
    a full schema is embedded for a previously unknown node type.
    """
    if node_type in base_schema.types:
        logger.debug(
            "Node type #%d: embedded delta-schema with %d fields",
            node_type,
            field_count,
        )
        old_fields = base_schema.types[node_type].fields
        merged_fields: list[FieldDef] = []
        old_index = 0

        while True:
            instruction = reader.stream.read(1)

            if instruction == b"Z":
                break
            if instruction == b"C":
                merged_fields.append(old_fields[old_index])
                old_index += 1
                continue
            if instruction == b"D":
                old_index += 1
                continue
            if instruction == b"I":
                inserted = parse_embedded_field(reader)
                logger.debug("  Insert: %s", inserted)
                # HACK: in observed files, inserted n_elements can be off by +1.
                inserted.n_elements = (
                    inserted.n_elements - 1
                    if inserted.n_elements > 2
                    else inserted.n_elements
                )
                merged_fields.append(to_field_def(inserted))
                continue
            if instruction == b"A":
                appended = parse_embedded_field(reader)
                logger.debug("  Append: %s", appended)
                merged_fields.append(to_field_def(appended))
                continue

            raise ValueError(f"Unknown delta-schema instruction: {instruction!r}")

        return merged_fields

    logger.debug(
        "Node type #%d: embedded full schema with %d fields", node_type, field_count
    )
    node_name = reader.str_u8_len()
    description = reader.str_u8_len()
    logger.debug("  Type name: %s", node_name)
    logger.debug("  Description: %s", description)

    embedded_fields = [parse_embedded_field(reader) for _ in range(field_count)]
    for fld in embedded_fields:
        logger.debug("  Field: %s", fld)

    fields = [to_field_def(fld) for fld in embedded_fields]
    variable = embedded_fields[-1].xmt_code if embedded_fields else False

    base_schema.types[node_type] = TypeDef(
        node_type=node_type,
        node_name=node_name,
        description=description,
        variable=variable,
        fields=fields,
    )
    return fields


def _clone_schema(base_schema: Schema) -> Schema:
    """Copy schema types so embedded full-schemas don't mutate the shared base."""
    return Schema(
        name=base_schema.name,
        types={
            k: TypeDef(
                node_type=v.node_type,
                node_name=v.node_name,
                description=v.description,
                variable=v.variable,
                fields=v.fields.copy(),
            )
            for k, v in base_schema.types.items()
        },
    )


@dataclass
class Document:
    """A round-trippable Parasolid file: decoded nodes plus the raw framing needed
    to re-serialize them byte-for-byte (see psparser.writer.write_document).

    `header` and `terminator` are captured verbatim; `schema_blobs` holds each
    node type's raw embedded-schema bytes (u8 field-count + payload), re-emitted
    once at the type's first occurrence; `layouts` and `variable` give the
    resolved per-type field layout used to encode each node's fields.
    """

    header: bytes
    nodes: list[dict]
    layouts: dict[int, list[FieldDef]]
    variable: dict[int, bool]
    schema_blobs: dict[int, bytes]
    terminator: bytes
    modeler_version: str = ""
    schema_name: str = ""
    schema_min_type: int = 0
    schema_max_type: int = 0


def read_document(stream: BinaryIO, base_schema: Schema) -> Document:
    """Parse a Parasolid binary file, capturing everything needed to rewrite it.

    Decodes every node (like `parse_ps`) and additionally retains the raw header,
    per-type embedded-schema blobs, resolved field layouts, and the terminator.
    """
    data = stream.read()
    buf = io.BytesIO(data)
    reader = Reader(buf)

    modeler_version, schema_name, schema_min_type, schema_max_type = parse_file_header(
        reader
    )
    header = data[: buf.tell()]
    logger.debug("Modeler version: %s; schema: %s", modeler_version, schema_name)

    if not schema_name.endswith(base_schema.name):
        raise ValueError(
            f"File schema name '{schema_name}' does not match expected base "
            f"schema '{base_schema.name}'"
        )

    base_schema = _clone_schema(base_schema)
    layouts: dict[int, list[FieldDef]] = {}
    variable: dict[int, bool] = {}
    schema_blobs: dict[int, bytes] = {}
    nodes: list[dict] = []

    while True:
        record_start = buf.tell()
        node_type = reader.i16()

        if node_type == 1:
            reader.i16()  # partition value
            terminator = data[record_start:]
            if buf.read(1) != b"":
                raise ValueError("Expected end of file after termination node")
            logger.debug("Terminator reached; %d nodes", len(nodes))
            return Document(
                header=header,
                nodes=nodes,
                layouts=layouts,
                variable=variable,
                schema_blobs=schema_blobs,
                terminator=terminator,
                modeler_version=modeler_version,
                schema_name=schema_name,
                schema_min_type=schema_min_type,
                schema_max_type=schema_max_type,
            )

        if node_type > schema_max_type:
            raise ValueError(
                f"Invalid node type {node_type}; max allowed is {schema_max_type}"
            )

        if node_type not in layouts:
            blob_start = buf.tell()
            field_count = reader.u8()
            if field_count == 255:
                base_type = base_schema.types.get(node_type)
                if base_type is None:
                    raise ValueError(
                        f"Node type #{node_type} missing from base schema and no "
                        "embedded schema provided"
                    )
                layouts[node_type] = base_type.fields
            else:
                layouts[node_type] = resolve_node_schema(
                    reader, node_type, field_count, base_schema
                )
            schema_blobs[node_type] = data[blob_start : buf.tell()]
            variable[node_type] = base_schema.types[node_type].variable

        node_fields = layouts[node_type]
        node_type_def = base_schema.types[node_type]

        node: dict[str, object] = {
            "node_type": node_type,
            "node_name": node_type_def.node_name,
        }

        repeat_count = 0
        if node_type_def.variable:
            repeat_count = reader.i32()
            node["count"] = repeat_count

        node["id"] = reader.i16()

        last_field_index = len(node_fields) - 1
        for idx, fdef in enumerate(node_fields):
            node[fdef.name] = (
                fdef.read(reader, repeat_count)
                if node_type_def.variable and idx == last_field_index
                else fdef.read(reader)
            )

        nodes.append(node)


def parse_ps(stream: BinaryIO, base_schema: Schema) -> list[dict]:
    """Parse one Parasolid binary file and return its decoded nodes.

    Thin wrapper over `read_document`; use `read_document` when you also need the
    raw framing (header, schema blobs, terminator) to re-serialize the file.
    """
    return read_document(stream, base_schema).nodes
