"""ASCII tree renderer for Parasolid XT node graphs."""

# Each node type uses a specific field to point at its topological parent.
_PARENT_FIELD: dict[str, str] = {
    "SHELL":    "region",
    "FACE":     "shell",
    "LOOP":     "face",
    "HALFEDGE": "loop",
    "FIN":      "loop",
    "REGION":   "body",
    "INSTANCE": "assembly",
}

# Each node type whose field explicitly lists child node ID(s).
# The field value may be None (absent), a single int ID, or a list of int IDs.
_CHILD_FIELD: dict[str, list[str]] = {
    "INSTANCE": ["part"],
    "ATTRIBUTE": ["definition", "fields"],
    "ATTRIB_DEF": ["identifier", "field_names"],
    "LIST": ["list_block"],
    "POINTER_LIS_BLOCK": ["entries"],
    "INTERSECTION": ["surface", "chart", "start", "end", "intersection_data"],
    "B_SURFACE": ["nurbs", "data"],
    "B_CURVE": ["nurbs", "data"],
    "NURBS_SURF": ["bspline_vertices", "u_knot_mult", "v_knot_mult", "u_knots", "v_knots"],
    "NURBS_CURVE": ["bspline_vertices", "knot_mult", "knots"],
    "SP_CURVE": ["surface", "b_curve"],
    "HALFEDGE": ["edge", "curve"],
}

# Types that are valid top-level roots with no expected parent.
_ROOT_TYPES = {"ASSEMBLY", "WORLD"}


def annotate(node: dict) -> str | None:
    if node["node_name"] == "ATT_DEF_ID":
        return node["string"]

    if node["node_name"] in {"CHAR_VALUES", "UNICODE_VALUES", "REAL_VALUES"}:
        return str(node["values"])

    return None


def build_tree(
    nodes: list[dict],
) -> tuple[list[int], dict[int, list[int]], dict[int, dict], int, list[int]]:
    """Build a topology-aware parent->children map from a node list.

    Known topology types use their specific parent-pointer field. Known root
    types (BODY, ASSEMBLY) are placed at the top level. Everything else tries
    the 'geometric_owner' field first, then falls back to 'owner': if either
    resolves, the node is attached as a child; otherwise it is excluded from
    the tree.

    Nodes whose type appears in _CHILD_FIELD have that field's value(s) added
    as explicit children. The field may be absent/None, a single int ID, or a
    list of int IDs.

    Returns (roots, children, by_id, fallback_count, unknown_ids).
    """
    by_id = {n["id"]: n for n in nodes}
    children: dict[int, list[int]] = {n["id"]: [] for n in nodes}
    roots: list[int] = []
    fallback = 0
    unknown: list[int] = []

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
            pid = None

            geometric_owner = node.get("geometric_owner")
            if isinstance(geometric_owner, int) and geometric_owner != 1:
                pid = geometric_owner
            else:
                owner = node.get("owner")
                if isinstance(owner, int) and owner != 1:
                    pid = owner

            if pid is not None:
                children[pid].append(node["id"])
                fallback += 1
            else:
                unknown.append(node["id"])

    # Attach explicit down-link children declared in _CHILD_FIELD.
    # Any unknown node that gets referenced here is no longer unplaced -
    # remove it from the unknown list.
    child_placed: set[int] = set()
    for node in nodes:
        name = node["node_name"]
        if name not in _CHILD_FIELD:
            continue
        fields = _CHILD_FIELD[name]
        for field in fields:
            val = node.get(field)
            if val is None:
                continue
            refs = val if isinstance(val, list) else [val]
            for ref in refs:
                if isinstance(ref, int) and ref in by_id:
                    children[node["id"]].append(ref)
                    child_placed.add(ref)
    unknown = [nid for nid in unknown if nid not in child_placed]

    return roots, children, by_id, fallback, unknown


def _lines(node_id: int, by_id, children, prefix: str, is_last: bool, seen: set[int]):
    node = by_id[node_id]
    branch = "\\-- " if is_last else "+-- "
    label = f"{node['node_name']}#{node['id']}"

    annotation = annotate(node)
    if annotation:
        label += f" [{annotation}]"

    if node_id in seen:
        # Already expanded elsewhere - show a back-reference marker only.
        yield prefix + branch + label + " (seen)"
        return

    seen.add(node_id)
    yield prefix + branch + label
    child_prefix = prefix + ("    " if is_last else "|   ")
    kids = children.get(node_id, [])
    for i, kid in enumerate(kids):
        yield from _lines(kid, by_id, children, child_prefix, i == len(kids) - 1, seen)


def render_tree(roots, children, by_id) -> str:
    """Render the node tree as an ASCII string with NAME#id labels.

    Nodes that appear more than once in the tree (shared references) are
    rendered in full only on their first occurrence. Subsequent occurrences
    show the label followed by '(seen)' and no children.
    """
    lines = []
    seen: set[int] = set()
    for root_id in roots:
        node = by_id[root_id]
        label = f"{node['node_name']}#{node['id']}"
        if root_id in seen:
            continue
        seen.add(root_id)
        lines.append(label)
        kids = children.get(root_id, [])
        for i, kid in enumerate(kids):
            lines.extend(_lines(kid, by_id, children, "", i == len(kids) - 1, seen))
    return "\n".join(lines)
