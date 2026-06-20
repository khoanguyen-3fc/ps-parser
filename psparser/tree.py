"""Doc-faithful Parasolid B-rep topology tree builder and ASCII renderer.

Builds a parent→children map by walking the exact pointer chains documented in
Parasolid V35 (xt_chap.05 / xt_chap.06), not by heuristic. Validates back-link
consistency (next/previous, forward/backward, etc.) and reports violations.

Key traversal rules
-------------------
- ASSEMBLY → INSTANCE chain (next_in_part / prev_in_part)
- INSTANCE.part → BODY or nested ASSEMBLY
- BODY → REGION chain (next/previous), EDGE chain, VERTEX chain
- REGION → SHELL chain (next)
- SHELL → back-face chain (face/next/previous), front-face chain
          (front_face/next_front/previous_front), wireframe EDGE chain
- FACE → LOOP chain (next); FACE.surface → geometry (traverse_geom)
- LOOP → fin ring (halfedge → forward / backward)
- FIN  → edge/vertex as cross-refs (seen); FIN.curve for tolerant fins
- EDGE → curve via traverse_geom; edge-fin ring validated
- VERTEX → POINT; vertex-fin chain validated
- Geometry → compound sub-nodes via _GEOM_CHILDREN; GEOMETRIC_OWNER ring;
  attributes_features chain
- ATTRIBUTE → definition (ATTRIB_DEF → ATT_DEF_ID); fields[] value nodes
- BODY/ASSEMBLY → attribute_chains LIST → POINTER_LIS_BLOCK → entry cross-refs
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Chain-walking helpers (module-level, stateless)
# ──────────────────────────────────────────────────────────────────────────────

def _ref(node: Optional[dict], field: str, by_id: dict) -> Optional[dict]:
    """Resolve one pointer field on node → target node, or None."""
    if node is None:
        return None
    val = node.get(field)
    return by_id.get(val) if isinstance(val, int) else None


def _walk(
    head_id,
    next_field: str,
    by_id: dict,
    *,
    prev_field: Optional[str] = None,
    violations: list,
) -> list:
    """Walk a linked-list spine (head → next_field → … → null).

    Validates back-link consistency when prev_field is given:
    for each step A→B, asserts B.prev_field == A.id.
    """
    result = []
    seen: set = set()
    cur = by_id.get(head_id) if isinstance(head_id, int) else None
    prev = None

    while cur is not None:
        nid = cur["id"]
        if nid in seen:
            violations.append(
                f"CYCLE at {cur['node_name']}#{nid} in '{next_field}' chain"
            )
            break
        seen.add(nid)
        if prev_field and prev is not None:
            back = cur.get(prev_field)
            if back != prev["id"]:
                violations.append(
                    f"{cur['node_name']}#{nid}.{prev_field}={back!r},"
                    f" expected {prev['id']}"
                )
        result.append(cur)
        prev = cur
        nxt = cur.get(next_field)
        cur = by_id.get(nxt) if isinstance(nxt, int) else None

    return result


def _ring(
    start_id,
    step_field: str,
    by_id: dict,
    *,
    back_field: Optional[str] = None,
    violations: list,
) -> list:
    """Walk a ring (start → step_field → … → start).

    Handles isolated elements (start.step == start) and validates back_field
    at each step and at ring wrap-around.
    """
    if not isinstance(start_id, int) or start_id not in by_id:
        return []

    result = []
    first_id = start_id
    seen_mid: set = set()
    cur = by_id[start_id]
    prev = None

    while True:
        nid = cur["id"]
        if back_field and prev is not None:
            back = cur.get(back_field)
            if back != prev["id"]:
                violations.append(
                    f"{cur['node_name']}#{nid}.{back_field}={back!r},"
                    f" expected {prev['id']}"
                )
        result.append(cur)
        prev = cur

        step_id = cur.get(step_field)
        if not isinstance(step_id, int) or step_id not in by_id:
            violations.append(
                f"DANGLING '{step_field}' from {cur['node_name']}#{nid}: {step_id!r}"
            )
            break

        if step_id == first_id:
            if back_field:
                start_node = by_id[first_id]
                back = start_node.get(back_field)
                if back != cur["id"]:
                    violations.append(
                        f"{start_node['node_name']}#{first_id}.{back_field}={back!r},"
                        f" expected {cur['id']} (ring wrap-around)"
                    )
            break

        if step_id in seen_mid:
            violations.append(
                f"UNEXPECTED CYCLE at id={step_id} in '{step_field}' ring"
            )
            break
        seen_mid.add(step_id)
        cur = by_id[step_id]

    return result


def _is_dummy(fin: dict) -> bool:
    """Dummy fins have loop=forward=backward=curve=next_at_vx all null (§5.3.9.1)."""
    return all(fin.get(f) is None for f in ("loop", "forward", "backward", "curve", "next_at_vx"))


# ──────────────────────────────────────────────────────────────────────────────
# Tree builder
# ──────────────────────────────────────────────────────────────────────────────

# Compound geometry types and the child-pointer fields they carry.
# Field values may be a single int or a list of ints.
_GEOM_CHILDREN: dict = {
    "INTERSECTION":  ["surface", "chart", "start", "end", "intersection_data"],
    "B_SURFACE":     ["nurbs", "data"],
    "NURBS_SURF":    ["bspline_vertices", "u_knot_mult", "v_knot_mult", "u_knots", "v_knots"],
    "B_CURVE":       ["nurbs", "data"],
    "NURBS_CURVE":   ["bspline_vertices", "knot_mult", "knots"],
    "SP_CURVE":      ["surface", "b_curve"],
    "TRIMMED_CURVE": ["basis_curve"],
    "BLENDED_EDGE":  ["surface", "spine"],
}


def build_tree(
    nodes: list,
) -> tuple:
    """Build a doc-faithful parent→children map from a node list.

    Walks every documented Parasolid pointer chain with back-link validation.
    No geometric_owner/owner heuristic.

    Returns (roots, children, by_id, violations, placed) where:
      roots      - list of top-level node ids
      children   - dict[int, list[int]] parent → children
      by_id      - dict[int, dict] id → node
      violations - list[str] link-consistency issues detected
      placed     - set[int] all node ids reached by traversal
    """
    by_id: dict = {n["id"]: n for n in nodes}
    children: dict = defaultdict(list)
    roots: list = []
    violations: list = []
    placed: set = set()

    # ── placement helpers ──────────────────────────────────────────────────────

    def _place(parent_id, child_id: int) -> None:
        placed.add(child_id)
        if parent_id is None:
            if child_id not in roots:
                roots.append(child_id)
        else:
            lst = children[parent_id]
            if child_id not in lst:
                lst.append(child_id)

    def _xref(parent_id: int, child_id: int) -> None:
        """Cross-reference: add child without marking placed (renders as '(seen)')."""
        lst = children[parent_id]
        if child_id not in lst:
            lst.append(child_id)

    # ── attribute / geometric-owner helpers ────────────────────────────────────

    def _geom_owner_ring(geom_node: dict, parent_id: int) -> None:
        """Walk GEOMETRIC_OWNER ring (§5.2.9): geometry.geometric_owner → ring via next/previous."""
        for go in _ring(
            geom_node.get("geometric_owner"), "next", by_id,
            back_field="previous", violations=violations,
        ):
            _place(parent_id, go["id"])

    def _attrib_def(ad: dict, parent_id: int) -> None:
        """Place ATTRIB_DEF and its ATT_DEF_ID identifier (§5.4.5)."""
        adid = ad["id"]
        already = adid in placed
        _place(parent_id, adid)
        if already:
            return
        if ident := _ref(ad, "identifier", by_id):
            _place(adid, ident["id"])

    def _attr_chain(entity: dict, parent_id: int) -> None:
        """Walk attributes_features chain and expand ATTRIBUTEs (§5.4.6).

        Each ATTRIBUTE: definition → ATTRIB_DEF → ATT_DEF_ID; fields[] → value nodes.
        """
        for attr in _walk(
            entity.get("attributes_features"), "next", by_id,
            prev_field="previous", violations=violations,
        ):
            aid = attr["id"]
            already = aid in placed
            _place(parent_id, aid)
            if already:
                continue
            if attr["node_name"] == "ATTRIBUTE":
                if ad := _ref(attr, "definition", by_id):
                    _attrib_def(ad, aid)
                val = attr.get("fields")
                if val is not None:
                    refs = val if isinstance(val, list) else [val]
                    for ref_id in refs:
                        if isinstance(ref_id, int) and ref_id in by_id:
                            _place(aid, ref_id)

    def _attr_chains_list(entity: dict, parent_id: int) -> None:
        """Traverse attribute_chains LIST → POINTER_LIS_BLOCK → entries (§5.4.6).

        entries are per-type ATTRIBUTE heads (already placed; shown as cross-refs).
        """
        ac = entity.get("attribute_chains")
        if not isinstance(ac, int) or ac not in by_id:
            return
        lst = by_id[ac]
        list_id = lst["id"]
        _place(parent_id, list_id)
        for plb in _walk(lst.get("list_block"), "next_block", by_id, violations=violations):
            plb_id = plb["id"]
            already = plb_id in placed
            _place(list_id, plb_id)
            if already:
                continue
            for entry_id in (plb.get("entries") or []):
                if isinstance(entry_id, int) and entry_id in by_id:
                    _xref(plb_id, entry_id)

    # ── geometry traversal ────────────────────────────────────────────────────

    def traverse_geom(node: dict, parent_id: int) -> None:
        """Place a geometry node and recursively expand compound sub-nodes."""
        nid = node["id"]
        already_placed = nid in placed
        _place(parent_id, nid)
        if already_placed:
            return
        for field in _GEOM_CHILDREN.get(node["node_name"], []):
            val = node.get(field)
            if val is None:
                continue
            refs = val if isinstance(val, list) else [val]
            for ref_id in refs:
                if isinstance(ref_id, int) and ref_id in by_id:
                    traverse_geom(by_id[ref_id], nid)
        _geom_owner_ring(node, nid)
        _attr_chain(node, nid)

    # ── topology traversal ────────────────────────────────────────────────────

    def traverse_world(world: dict, parent_id) -> None:
        wid = world["id"]
        _place(parent_id, wid)
        # WORLD.assembly → top-level assembly
        if asm := _ref(world, "assembly", by_id):
            traverse_assembly(asm, wid)
        # WORLD.body → body chain (bodies directly attached to world, walked via next)
        for body in _walk(world.get("body"), "next", by_id,
                          prev_field="previous", violations=violations):
            traverse_body(body, wid)
        # WORLD.attrib_def → global ATTRIB_DEF chain (definitions registered in this part)
        for ad in _walk(world.get("attrib_def"), "next", by_id, violations=violations):
            _attrib_def(ad, wid)
        _attr_chain(world, wid)

    def traverse_assembly(asm: dict, parent_id) -> None:
        aid = asm["id"]
        _place(parent_id, aid)
        for inst in _walk(
            asm.get("sub_instance"), "next_in_part", by_id,
            prev_field="prev_in_part", violations=violations,
        ):
            traverse_instance(inst, aid)
        _attr_chain(asm, aid)
        _attr_chains_list(asm, aid)

    def traverse_instance(inst: dict, parent_id: int) -> None:
        iid = inst["id"]
        _place(parent_id, iid)
        part = _ref(inst, "part", by_id)
        if part is None:
            _attr_chain(inst, iid)
            return
        if part["node_name"] == "BODY":
            traverse_body(part, iid)
        elif part["node_name"] == "ASSEMBLY":
            traverse_assembly(part, iid)
        _attr_chain(inst, iid)

    def traverse_body(body: dict, parent_id) -> None:
        bid = body["id"]
        _place(parent_id, bid)
        for region in _walk(
            body.get("region"), "next", by_id,
            prev_field="previous", violations=violations,
        ):
            traverse_region(region, bid)
        for edge in _walk(
            body.get("edge"), "next", by_id,
            prev_field="previous", violations=violations,
        ):
            traverse_edge(edge, bid)
        for vertex in _walk(
            body.get("vertex"), "next", by_id,
            prev_field="previous", violations=violations,
        ):
            traverse_vertex(vertex, bid)
        for cb in _walk(
            body.get("child"), "next", by_id,
            prev_field="previous", violations=violations,
        ):
            traverse_body(cb, bid)
        _attr_chain(body, bid)
        _attr_chains_list(body, bid)

    def traverse_region(region: dict, parent_id: int) -> None:
        rid = region["id"]
        _place(parent_id, rid)
        for shell in _walk(region.get("shell"), "next", by_id, violations=violations):
            traverse_shell(shell, rid)
        _attr_chain(region, rid)

    def traverse_shell(shell: dict, parent_id: int) -> None:
        sid = shell["id"]
        _place(parent_id, sid)
        for face in _walk(
            shell.get("face"), "next", by_id,
            prev_field="previous", violations=violations,
        ):
            traverse_face(face, sid)
        for face in _walk(
            shell.get("front_face"), "next_front", by_id,
            prev_field="previous_front", violations=violations,
        ):
            traverse_face(face, sid)
        for edge in _walk(
            shell.get("edge"), "next", by_id,
            prev_field="previous", violations=violations,
        ):
            traverse_edge(edge, sid)
        if acorn := _ref(shell, "vertex", by_id):
            traverse_vertex(acorn, sid)
        _attr_chain(shell, sid)

    def traverse_face(face: dict, parent_id: int) -> None:
        fid = face["id"]
        already_placed = fid in placed
        _place(parent_id, fid)
        if already_placed:
            return
        for loop in _walk(face.get("loop"), "next", by_id, violations=violations):
            traverse_loop(loop, fid)
        if surf := _ref(face, "surface", by_id):
            traverse_geom(surf, fid)
        _attr_chain(face, fid)

    def traverse_loop(loop: dict, parent_id: int) -> None:
        lid = loop["id"]
        _place(parent_id, lid)
        for fin in _ring(
            loop.get("halfedge"), "forward", by_id,
            back_field="backward", violations=violations,
        ):
            if not _is_dummy(fin):
                traverse_fin(fin, lid)
        _attr_chain(loop, lid)

    def traverse_fin(fin: dict, parent_id: int) -> None:
        fid = fin["id"]
        _place(parent_id, fid)
        if edge := _ref(fin, "edge", by_id):
            _xref(fid, edge["id"])
        if vertex := _ref(fin, "vertex", by_id):
            _xref(fid, vertex["id"])
        if fin_curve := _ref(fin, "curve", by_id):
            traverse_geom(fin_curve, fid)
        _attr_chain(fin, fid)

    def traverse_edge(edge: dict, parent_id: int) -> None:
        eid = edge["id"]
        already_placed = eid in placed
        _place(parent_id, eid)
        if already_placed:
            return
        if curve := _ref(edge, "curve", by_id):
            traverse_geom(curve, eid)
        _ring(edge.get("halfedge"), "other", by_id, violations=violations)
        _attr_chain(edge, eid)

    def traverse_vertex(vertex: dict, parent_id: int) -> None:
        vid = vertex["id"]
        already_placed = vid in placed
        _place(parent_id, vid)
        if already_placed:
            return
        if pt := _ref(vertex, "point", by_id):
            _place(vid, pt["id"])
        _walk(vertex.get("halfedge"), "next_at_vx", by_id, violations=violations)
        _attr_chain(vertex, vid)

    # ── entry point ────────────────────────────────────────────────────────────

    # WORLD node is the authoritative root when present (Parasolid "part" container).
    world_nodes = [n for n in nodes if n["node_name"] == "WORLD"]
    for world in world_nodes:
        traverse_world(world, None)

    # Fallback for files without a WORLD node: find root assemblies, then
    # any standalone bodies not yet reached via an assembly chain.
    part_ids: set = {
        n["part"]
        for n in nodes
        if n["node_name"] == "INSTANCE" and isinstance(n.get("part"), int)
    }
    all_assemblies = [n for n in nodes if n["node_name"] == "ASSEMBLY"]
    root_assemblies = [a for a in all_assemblies if a["id"] not in part_ids]
    if not root_assemblies:
        root_assemblies = [a for a in all_assemblies if a.get("previous") is None]

    for asm in root_assemblies:
        if asm["id"] not in placed:
            traverse_assembly(asm, None)

    for n in nodes:
        if n["node_name"] == "BODY" and n["id"] not in placed:
            traverse_body(n, None)

    # Validate geometry-sharing chains (not added to tree — would create cycles)
    for n in nodes:
        nid = n["id"]
        if n["node_name"] == "EDGE":
            nxt_id = n.get("next_on_curve")
            if isinstance(nxt_id, int) and nxt_id in by_id:
                back = by_id[nxt_id].get("previous_on_curve")
                if back != nid:
                    violations.append(
                        f"EDGE#{nxt_id}.previous_on_curve={back!r}, expected {nid}"
                    )
        elif n["node_name"] == "FACE":
            nxt_id = n.get("next_on_surface")
            if isinstance(nxt_id, int) and nxt_id in by_id:
                back = by_id[nxt_id].get("previous_on_surface")
                if back != nid:
                    violations.append(
                        f"FACE#{nxt_id}.previous_on_surface={back!r}, expected {nid}"
                    )

    return roots, dict(children), by_id, violations, placed


# ──────────────────────────────────────────────────────────────────────────────
# Tree rendering
# ──────────────────────────────────────────────────────────────────────────────

_BODY_TYPE   = {1: "solid", 2: "wire", 3: "sheet", 6: "general"}
_REGION_TYPE = {"S": "solid", "V": "void"}


def annotate(node: dict) -> Optional[str]:
    """Return a short annotation string for a node, or None."""
    name = node["node_name"]
    if name == "BODY":
        return _BODY_TYPE.get(node.get("body_type")) or None
    if name == "REGION":
        return _REGION_TYPE.get(node.get("type")) or None
    if name in ("FACE", "HALFEDGE"):
        s = node.get("sense")
        return f"sense={s}" if s else None
    if name == "ATT_DEF_ID":
        return node.get("string")
    if name in ("CHAR_VALUES", "UNICODE_VALUES", "REAL_VALUES"):
        return str(node.get("values", ""))
    return None


def _lines(node_id: int, by_id: dict, children: dict, prefix: str, is_last: bool, seen: set):
    node = by_id[node_id]
    branch = "\\-- " if is_last else "+-- "
    label = f"{node['node_name']}#{node_id}"
    ann = annotate(node)
    if ann:
        label += f" [{ann}]"
    if node_id in seen:
        yield prefix + branch + label + " (seen)"
        return
    seen.add(node_id)
    yield prefix + branch + label
    child_prefix = prefix + ("    " if is_last else "|   ")
    kids = children.get(node_id, [])
    for i, kid in enumerate(kids):
        yield from _lines(kid, by_id, children, child_prefix, i == len(kids) - 1, seen)


def render_tree(roots: list, children: dict, by_id: dict) -> str:
    """Render the node tree as an ASCII string with NAME#id labels.

    Nodes that appear more than once (shared references) are rendered in full
    only on their first occurrence; subsequent occurrences show '(seen)'.
    """
    lines = []
    seen: set = set()
    for root_id in roots:
        if root_id in seen:
            continue
        node = by_id[root_id]
        label = f"{node['node_name']}#{root_id}"
        ann = annotate(node)
        if ann:
            label += f" [{ann}]"
        seen.add(root_id)
        lines.append(label)
        kids = children.get(root_id, [])
        for i, kid in enumerate(kids):
            lines.extend(_lines(kid, by_id, children, "", i == len(kids) - 1, seen))
    return "\n".join(lines)
