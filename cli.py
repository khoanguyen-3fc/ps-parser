"""Parse a Parasolid XT binary file and emit decoded nodes as JSON Lines."""

import argparse
import json
import logging
import sys
from pathlib import Path

from psparser import build_tree, load_schema, parse_ps, render_tree

DEFAULT_SCHEMA = Path(__file__).parent / "assets" / "sch_13006.s_t"


def main() -> None:
    """Main entry point for the command-line interface."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Parasolid binary file (.x_b)")
    ap.add_argument(
        "--schema",
        default=str(DEFAULT_SCHEMA),
        metavar="PATH",
        help="base schema file (default: assets/sch_13006.s_t)",
    )
    ap.add_argument(
        "--tree",
        action="store_true",
        help="display node tree instead of JSON Lines (experimental)",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="write parser diagnostics to stderr",
    )
    args = ap.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    schema = load_schema(args.schema)

    with open(args.input, "rb") as f:
        nodes = parse_ps(f, schema)

    if args.tree:
        print("warning: --tree is experimental", file=sys.stderr)
        roots, children, by_id, unknown = build_tree(nodes)
        print(render_tree(roots, children, by_id))
        print(f"{unknown} nodes with unrecognized type (owner fallback).", file=sys.stderr)
    else:
        for node in nodes:
            print(json.dumps(node))

    print(f"{args.input}: {len(nodes)} nodes parsed cleanly.", file=sys.stderr)


if __name__ == "__main__":
    main()
