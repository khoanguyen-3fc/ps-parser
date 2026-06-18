# ps-parser

A dynamic, schema-aware parser for **Parasolid XT** binary part files (`.x_b`).

The parser reads the XT binary node stream against a base schema and resolves
per-node schemas on the fly — handling files that embed full or delta schema
definitions for node types that differ from (or extend) the base schema.

## Installation

No third-party dependencies — Python 3.10+ and the standard library only.

```bash
git clone https://github.com/khoanguyen-3fc/ps-parser.git
cd ps-parser
```

## Usage

```bash
# Default: emit the node list as JSON Lines (one object per line) to stdout
python cli.py samples/model.x_b

# Dump everything: full decoded fields per node on stdout,
# parser diagnostics on stderr
python cli.py --debug samples/model.x_b

# Use a different base schema
python cli.py --schema assets/sch_13006.s_t samples/model.x_b

# Display the topology tree
python cli.py --tree samples/model.x_b
```

### Output

- **Default** — one JSON Lines object per node with all decoded fields.
- **`--debug`** — same JSON Lines output, plus detailed parser diagnostics
  (header, schema resolution, per-field values) written to **stderr**.
- **`--tree`** — ASCII topology tree to stdout. Nodes are
  placed using type-specific parent pointers (`SHELL→body`, `FACE→shell`,
  `LOOP→face`, etc.). A count of nodes with unrecognized types is written to
  stderr.

Because the default output is JSON Lines, it composes with standard tools:

```bash
python cli.py samples/model.x_b | head -1 | python -m json.tool
```

## Default schema

The default base schema is [`assets/sch_13006.s_t`](assets/sch_13006.s_t)
(schema `13006`). Override it with `--schema`. The file's schema name must be
compatible with the base schema, or parsing aborts.

## Project layout

```
cli.py                 # command-line entry point
psparser/
  __init__.py          # public API
  reader.py            # binary readers + field-type codes
  schema.py            # schema data models + base/embedded schema parsing
  parser.py            # file header + dynamic node-stream decoder
  tree.py              # topology tree builder + ASCII renderer
assets/                # default base schema
```

## Using as a library

```python
from psparser import load_schema, parse_ps

schema = load_schema("assets/sch_13006.s_t")
with open("samples/model.x_b", "rb") as f:
    nodes = parse_ps(f, schema)
```

`parse_ps` returns a `list[dict]` of decoded nodes and emits diagnostics through
the `logging` module (no printing), so callers control verbosity.

## Reference

The primary reference for the Parasolid XT format is the official documentation:

- **Parasolid XT V35 documentation** — <http://www.q-solid.com/Parasolid_Docs_V35/xt_index.html>

## License

Released under the [MIT License](LICENSE). Feel free to use, modify, and distribute.
