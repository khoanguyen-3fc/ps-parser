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
        help="display node tree instead of JSON Lines",
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
        roots, children, by_id, violations, placed = build_tree(nodes)
        print(render_tree(roots, children, by_id))
        unreached = [n for n in nodes if n["id"] not in placed]
        print(f"Reached: {len(placed)}/{len(nodes)} nodes", file=sys.stderr)
        if unreached:
            names = ", ".join(
                f"{n['node_name']}#{n['id']}" for n in unreached[:20]
            )
            suffix = f" … and {len(unreached) - 20} more" if len(unreached) > 20 else ""
            print(f"Unreached ({len(unreached)}): {names}{suffix}", file=sys.stderr)
        if violations:
            print(f"Link violations ({len(violations)}):", file=sys.stderr)
            for v in violations[:10]:
                print(f"  {v}", file=sys.stderr)
            if len(violations) > 10:
                print(f"  … and {len(violations) - 10} more", file=sys.stderr)
    else:
        for node in nodes:
            print(json.dumps(node))

    print(f"{args.input}: {len(nodes)} nodes parsed cleanly.", file=sys.stderr)


if __name__ == "__main__":
    main()
