"""Schema data models and parsers for Parasolid XT node type definitions."""

from dataclasses import dataclass
import logging
import os
import re

from .reader import FieldType, READERS, Reader

logger = logging.getLogger(__name__)

_SCHEMA_NAME_RE = re.compile(r"KEY=SCH_(\w+)")

SCHEMA_TYPE_PATTERN = re.compile(
    r"^(?P<nodetype>\d+) (?P<nodename>[A-Z_]+); (?P<description>[^;]+); (?P<transmit>\d+) (?P<n_fields>\d+) (?P<variable>\d+) \n(?P<fields>(?:\D.+\n)*)",
    flags=re.MULTILINE,
)

SCHEMA_FIELD_PATTERN = re.compile(
    r"(?P<fieldname>[a-z]\w*); (?P<type>\w); (?P<transmit>\d+) (?P<nodeclass>\d+) (?P<n_elements>\d+) "
)


@dataclass(slots=True)
class FieldDef:
    """One transmitted schema field with decode metadata."""

    name: str
    field_type: FieldType
    node_class: int
    n_elements: int

    def read(self, reader: Reader, count_override: int = 1) -> object:
        """Decode this field from the stream.

        For variable-length nodes, the final field may use `count_override`.
        """
        read_one = READERS[self.field_type]
        count = count_override if count_override > 1 else self.n_elements

        if count > 1:
            if self.field_type is FieldType.CHAR:
                return reader.char(count)
            if self.field_type is FieldType.UTF16:
                return reader.utf16_be(count)
            return [read_one(reader) for _ in range(count)]

        return read_one(reader)


@dataclass(slots=True)
class TypeDef:
    """One node type description from a schema."""

    node_type: int
    node_name: str
    description: str
    variable: bool
    fields: list[FieldDef]


@dataclass(slots=True)
class Schema:
    """Collection of node type definitions keyed by node type id."""

    name: str
    types: dict[int, TypeDef]


@dataclass(slots=True)
class EmbeddedField:
    """Transient representation used while parsing embedded schema fields."""

    name: str
    ptr_class: int
    n_elements: int
    field_type: str
    xmt_code: bool = False


def parse_base_schema(schema_text: str) -> dict[int, TypeDef]:
    """Parse base schema text file into transmitted type definitions."""
    schema: dict[int, TypeDef] = {}

    for match in SCHEMA_TYPE_PATTERN.finditer(schema_text):
        try:
            transmitted = bool(int(match.group("transmit")))
            if not transmitted:
                continue

            node_type = int(match.group("nodetype"))
            node_name = match.group("nodename")
            expected_count = int(match.group("n_fields"))
            description = match.group("description")
            variable = bool(int(match.group("variable")))

            fields_raw = match.group("fields")
            fields: list[FieldDef] = []
            parsed_count = 0

            for field_match in SCHEMA_FIELD_PATTERN.finditer(fields_raw):
                parsed_count += 1
                if not bool(int(field_match.group("transmit"))):
                    continue

                fields.append(
                    FieldDef(
                        name=field_match.group("fieldname"),
                        field_type=FieldType(field_match.group("type")),
                        node_class=int(field_match.group("nodeclass")),
                        n_elements=int(field_match.group("n_elements")),
                    )
                )

            if parsed_count != expected_count:
                logger.warning(
                    "expected %d fields for node type %d, parsed %d.",
                    expected_count,
                    node_type,
                    parsed_count,
                )

            schema[node_type] = TypeDef(
                node_type=node_type,
                node_name=node_name,
                description=description,
                variable=variable,
                fields=fields,
            )
        except Exception as exc:
            logger.error("Error parsing node type %s: %s", match.group("nodetype"), exc)

    return schema


def load_schema(path: str) -> Schema:
    """Read a schema text file; extract the schema name from its KEY= header."""
    with open(path, "r", encoding="utf-8") as f:
        schema_text = f.read()
    m = _SCHEMA_NAME_RE.search(schema_text[:1024])
    if m:
        name = m.group(1)
    else:
        stem = os.path.splitext(os.path.basename(path))[0]
        name = stem.split("_", 1)[-1] if "_" in stem else stem
    return Schema(name=name, types=parse_base_schema(schema_text))


def parse_embedded_field(reader: Reader) -> EmbeddedField:
    """Parse one field definition from embedded schema data."""
    name = reader.str_u8_len()
    ptr_class = reader.u16()
    n_elements = reader.u16()
    field_type = reader.str_u8_len() if ptr_class == 0 else "p"

    xmt_code = False
    if n_elements == 2:
        xmt_code = reader.bool8()

    return EmbeddedField(
        name=name,
        ptr_class=ptr_class,
        n_elements=n_elements,
        field_type=field_type,
        xmt_code=xmt_code,
    )


def to_field_def(field: EmbeddedField) -> FieldDef:
    """Convert an embedded field descriptor to runtime decode metadata."""
    return FieldDef(
        name=field.name,
        field_type=FieldType(field.field_type),
        node_class=field.ptr_class,
        n_elements=field.n_elements,
    )
