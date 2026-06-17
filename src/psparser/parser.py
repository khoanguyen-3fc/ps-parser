"""Dynamic, schema-aware decoder for Parasolid XT binary part files."""

import logging
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

    modeler_version = reader.str_u32_len()
    schema_name = reader.str_u32_len()
    schema_max_type = reader.u16()
    schema_min_type = reader.u16()

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


def parse_ps(stream: BinaryIO, base_schema: Schema) -> list[dict]:
    """Parse one Parasolid binary file using dynamic schema resolution.

    Returns the list of decoded nodes. Diagnostic detail is emitted through the
    module logger at DEBUG level rather than printed.
    """
    reader = Reader(stream)

    modeler_version, schema_name, schema_min_type, schema_max_type = parse_file_header(
        reader
    )
    logger.debug("Modeler version: %s", modeler_version)
    logger.debug("Schema name: %s", schema_name)

    if not schema_name.endswith(base_schema.name):
        raise ValueError(
            f"File schema name '{schema_name}' does not match expected base schema '{base_schema.name}'"
        )

    logger.debug(
        "File schema '%s' compatible with base schema '%s'",
        schema_name,
        base_schema.name,
    )
    logger.debug("Schema type range: %d to %d", schema_min_type, schema_max_type)

    # clone base schema types to allow in-place updates when full embedded schemas are encountered
    base_schema = Schema(
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
    working_schema: dict[int, list[FieldDef]] = {}
    nodes: list[dict] = []

    while True:
        node_type = reader.u16()

        if node_type == 1:
            _partition = reader.u16()
            logger.debug("Terminator reached!")
            if reader.stream.read(1) != b"":
                raise ValueError("Expected end of file after termination node")
            break

        if node_type > schema_max_type:
            raise ValueError(
                f"Invalid node type {node_type}; max allowed is {schema_max_type}"
            )

        if node_type not in working_schema:
            field_count = reader.u8()

            if field_count == 255:
                logger.debug("Node type #%d: no embedded schema", node_type)
                base_type = base_schema.types.get(node_type)
                if base_type is None:
                    raise ValueError(
                        f"Node type #{node_type} missing from base schema and no embedded schema provided"
                    )
                working_schema[node_type] = base_type.fields
            else:
                working_schema[node_type] = resolve_node_schema(
                    reader, node_type, field_count, base_schema
                )

        node_fields = working_schema[node_type]
        node_type_def = base_schema.types[node_type]

        logger.debug(
            "Node type #%d: %s - %s",
            node_type,
            node_type_def.node_name,
            node_type_def.description,
        )

        node: dict[str, object] = {
            "node_type": node_type,
            "node_name": node_type_def.node_name,
        }

        repeat_count = 0
        if node_type_def.variable:
            repeat_count = reader.u32()
            node["count"] = repeat_count
            logger.debug(
                "Node type #%d: variable with %d instances", node_type, repeat_count
            )

        node_id = reader.u16()
        node["id"] = node_id
        logger.debug("Node type #%d: id %d", node_type, node_id)

        last_field_index = len(node_fields) - 1
        for idx, field in enumerate(node_fields):
            value = (
                field.read(reader, repeat_count)
                if node_type_def.variable and idx == last_field_index
                else field.read(reader)
            )
            node[field.name] = value
            logger.debug("  Field %s: %s", field.name, value)

        logger.debug("%s", node)
        nodes.append(node)

    return nodes
