"""psparser - dynamic, schema-aware parser for Parasolid XT binary part files."""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())

from .parser import parse_file_header, parse_ps, resolve_node_schema
from .tree import annotate, build_tree, render_tree
from .reader import FieldType, READERS, Reader
from .schema import (
    EmbeddedField,
    FieldDef,
    Schema,
    TypeDef,
    load_schema,
    parse_base_schema,
    parse_embedded_field,
    to_field_def,
)

__all__ = [
    "annotate",
    "build_tree",
    "render_tree",
    "EmbeddedField",
    "FieldDef",
    "FieldType",
    "READERS",
    "Reader",
    "Schema",
    "TypeDef",
    "load_schema",
    "parse_base_schema",
    "parse_embedded_field",
    "parse_file_header",
    "parse_ps",
    "resolve_node_schema",
    "to_field_def",
]
