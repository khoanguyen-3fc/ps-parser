"""ASCII tree renderer for Parasolid XT node graphs."""

# Each node type uses a specific field to point at its topological parent.
_PARENT_FIELD: dict[str, str] = {
    "SHELL":    "body",
    "FACE":     "shell",
    "LOOP":     "face",
    "HALFEDGE": "loop",
    "FIN":      "loop",
    "REGION":   "body",
    "INSTANCE": "assembly",
}

# Types that are valid top-level roots with no expected parent.
_ROOT_TYPES = {"BODY", "ASSEMBLY", "WORLD"}


def build_tree(
    nodes: list[dict],
) -> tuple[list[int], dict[int, list[int]], dict[int, dict], int]:
    """Build a topology-aware parent→children map from a node list.

    Known topology types use their specific parent-pointer field. Known root
    types (BODY, ASSEMBLY) are placed at the top level. Everything else tries
    the 'owner' field: if it resolves, the node is attached as a child;
    otherwise it is excluded from the tree.

    Returns (roots, children, by_id, unknown_count) where unknown_count is the
    number of nodes not in the known type tables.
    """
    by_id = {n["id"]: n for n in nodes}
    children: dict[int, list[int]] = {n["id"]: [] for n in nodes}
    roots: list[int] = []
    unknown = 0

    for node in nodes:
        name = node["node_name"]

        if name in _PARENT_FIELD:
            field = _PARENT_FIELD[name]
            val = node.get(field)
            pid = val if isinstance(val, int) and val in by_id else None
            if pid is None:
                roots.append(node["id"])
            else:
                children[pid].append(node["id"])

        elif name in _ROOT_TYPES:
            roots.append(node["id"])

        else:
            unknown += 1
            val = node.get("owner")
            if isinstance(val, int) and val in by_id:
                children[val].append(node["id"])
            # no valid owner → silently excluded from the tree

    return roots, children, by_id, unknown


def _lines(node_id, by_id, children, prefix, is_last):
    node = by_id[node_id]
    branch = "└── " if is_last else "├── "
    yield prefix + branch + f"{node['node_name']}#{node['id']}"
    child_prefix = prefix + ("    " if is_last else "│   ")
    kids = children.get(node_id, [])
    for i, kid in enumerate(kids):
        yield from _lines(kid, by_id, children, child_prefix, i == len(kids) - 1)


def render_tree(roots, children, by_id) -> str:
    """Render the node tree as an ASCII string with NAME#id labels."""
    lines = []
    for root_id in roots:
        node = by_id[root_id]
        lines.append(f"{node['node_name']}#{node['id']}")
        kids = children.get(root_id, [])
        for i, kid in enumerate(kids):
            lines.extend(_lines(kid, by_id, children, "", i == len(kids) - 1))
    return "\n".join(lines)
