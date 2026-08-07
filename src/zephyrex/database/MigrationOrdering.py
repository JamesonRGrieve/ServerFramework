"""FK-aware cross-extension migration ordering (Item 49).

Migration ordering is computed as a topological sort over the union of two
edge sources:

  (a) **Declared ``EXT_Dependency`` relationships** — for each non-optional
      ``EXT_Dependency(name="A")`` on extension ``B``, contribute the
      directed edge ``A -> B`` (A migrates first).
  (b) **FK-discovered references** — for each ``ForeignKey`` on a table
      owned by extension ``B`` that references a table owned by extension
      ``A`` (where ``A != B``), contribute the directed edge ``A -> B``.
      FK references between **core and extension** tables don't add edges;
      core always migrates first by structural rule (the migration runner
      sequences core before extensions unconditionally).

Cycles in the merged graph (for example, an FK from A.x → B.y combined
with an FK from B.z → A.w) raise :class:`MigrationOrderingError` whose
message names the offending extensions and the FK columns that closed
the cycle, plus a hint toward the recommended workaround (introduce a
join table owned by one of the extensions, rather than mutual direct
FKs).

This module does **not** import the migration runner, the SQLAlchemy
declarative base, or the extension registry. It receives:

  - ``metadata`` — any object exposing a ``tables: Dict[str, Table]``
    attribute (typically a SQLAlchemy ``MetaData`` whose tables have
    ``.info`` / ``.foreign_keys`` populated).
  - ``extension_dependencies`` — a mapping ``{ext_name: Iterable[str]}``
    where each value enumerates the names of extensions ``ext_name``
    explicitly depends on. Optional dependencies must be filtered out by
    the caller before the mapping is passed in.

Cross-references:
  - ``extensions/MigrationOwnership.py`` (Item 24): the canonical resolver
    consulted to map each ``Table`` to its owning extension.
  - ``database/MigrationSafety.py`` (Item 80): a separate concern — the
    expand/contract phase contract; ordering and safety run independently.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from zephyrex.extensions.MigrationOwnership import env_is_table_owned_by_extension


__all__ = [
    "MigrationOrderingError",
    "compute_migration_order",
]


class MigrationOrderingError(Exception):
    """Raised when FK-aware migration ordering detects a cycle.

    The error names both the cycle's extension nodes and the FK-induced
    edges (or declared-dependency edges) that closed the cycle.
    Attributes:
        cycle_extensions: ordered list of extension names along the cycle
        cycle_tables: list of ``(src_table, src_col, ref_table, ref_col)``
            tuples for the FK edges in the cycle (declared-dependency
            edges are not represented here — those appear only in
            ``__str__``).
    """

    def __init__(
        self,
        cycle_extensions: Sequence[str],
        cycle_tables: Sequence[Tuple[str, str, str, str]],
        edge_descriptions: Optional[Sequence[str]] = None,
    ):
        self.cycle_extensions = list(cycle_extensions)
        self.cycle_tables = list(cycle_tables)
        self.edge_descriptions = list(edge_descriptions or [])
        msg = self._format_message()
        super().__init__(msg)

    def _format_message(self) -> str:
        ext_chain = " -> ".join(self.cycle_extensions) if self.cycle_extensions else "<unknown>"
        edges = "; ".join(self.edge_descriptions) if self.edge_descriptions else "<no edge detail>"
        tables = (
            "; ".join(
                f"{s_t}.{s_c} -> {r_t}.{r_c}"
                for (s_t, s_c, r_t, r_c) in self.cycle_tables
            )
            if self.cycle_tables
            else "<no FK edges>"
        )
        return (
            "Cross-extension migration cycle detected (Item 49). "
            f"Cycle: {ext_chain}. "
            f"Edges: {edges}. "
            f"Offending FK tables: {tables}. "
            "Resolution: introduce a join table owned by one side rather "
            "than mutual direct FKs (see DB.Migrations.md)."
        )


def compute_migration_order(
    metadata: Any,
    extension_dependencies: Mapping[str, Iterable[str]],
) -> List[str]:
    """Topologically sort extensions for FK-aware migration ordering.

    Args:
        metadata: SQLAlchemy ``MetaData`` (or any object with a ``tables``
            ``Dict[str, Table]`` attribute) populated with every table
            whose ownership ought to be considered. Pass ``None`` to skip
            FK discovery entirely (only declared dependencies contribute
            edges).
        extension_dependencies: Mapping ``{ext_name: Iterable[str]}``
            enumerating the explicit (non-optional) ``EXT_Dependency``
            names per extension. Any extension that appears as a
            dependency target but not as a key is treated as a node with
            no outgoing edges. Pass ``{}`` to consider only FK-discovered
            edges.

    Returns:
        A list of extension names in a valid migration order. Core is
        **not** included in this list — the caller sequences core before
        the returned list. Unrelated extensions land in deterministic
        alphabetical order (Kahn's algorithm with sorted ready-set).

    Raises:
        MigrationOrderingError: when the merged graph contains a cycle.
    """
    # Build the node set from both edge sources so an extension that
    # appears only as a dep target (or only as a table owner) still gets
    # a node.
    nodes: Set[str] = set(extension_dependencies.keys())
    for deps in extension_dependencies.values():
        for d in deps:
            nodes.add(d)

    # Walk metadata once to harvest FK edges + extend the node set with
    # any owner that surfaces only via SA tables.
    fk_edges: List[Tuple[str, str, Tuple[str, str, str, str]]] = []
    if metadata is not None:
        tables = getattr(metadata, "tables", None) or {}
        for table_name, table in tables.items():
            owner = env_is_table_owned_by_extension(table)
            if owner is None:
                continue
            nodes.add(owner)
            for fk in getattr(table, "foreign_keys", ()) or ():
                ref_col = getattr(fk, "column", None)
                ref_table = getattr(ref_col, "table", None) if ref_col is not None else None
                if ref_table is None:
                    # Unresolved FK target (table not yet added to
                    # metadata). Skip — caller passed pre-resolution
                    # metadata; declared deps are still honored.
                    continue
                ref_owner = env_is_table_owned_by_extension(ref_table)
                if ref_owner is None or ref_owner == owner:
                    # Self-referential FKs and FKs into core add no
                    # cross-extension edges. Core sequencing is
                    # structural, not graph-based.
                    continue
                nodes.add(ref_owner)
                src_col_name = getattr(getattr(fk, "parent", None), "name", "<col>")
                ref_col_name = getattr(ref_col, "name", "<col>")
                fk_edges.append(
                    (
                        ref_owner,
                        owner,
                        (table_name, src_col_name, ref_table.name, ref_col_name),
                    )
                )

    # graph[B] = set of nodes A such that A -> B (i.e. A migrates before B).
    # Equivalently: B's predecessors. We use this shape for Kahn's algorithm
    # because each iteration pops a node with no remaining predecessors.
    sorted_nodes: List[str] = sorted(nodes)
    predecessors: Dict[str, Set[str]] = {n: set() for n in sorted_nodes}
    successors: Dict[str, Set[str]] = {n: set() for n in sorted_nodes}
    edge_provenance: Dict[Tuple[str, str], List[str]] = {}
    fk_edge_record: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = {}

    # (a) declared deps
    for ext, deps in extension_dependencies.items():
        for dep in deps:
            if dep == ext:
                # An extension declaring itself as a dependency is
                # malformed; treat as a no-op rather than a self-cycle.
                continue
            predecessors[ext].add(dep)
            successors[dep].add(ext)
            edge_provenance.setdefault((dep, ext), []).append(
                f"declared EXT_Dependency: {ext} depends on {dep}"
            )

    # (b) FK-discovered edges
    for src_ext, dst_ext, fk_record in fk_edges:
        if src_ext == dst_ext:
            continue
        predecessors[dst_ext].add(src_ext)
        successors[src_ext].add(dst_ext)
        edge_provenance.setdefault((src_ext, dst_ext), []).append(
            f"FK {fk_record[0]}.{fk_record[1]} -> {fk_record[2]}.{fk_record[3]}"
        )
        fk_edge_record.setdefault((src_ext, dst_ext), []).append(fk_record)

    # Kahn's algorithm with deterministic tie-breaking via sorted ready set.
    ready = sorted([n for n in sorted_nodes if not predecessors[n]])
    order: List[str] = []
    pred_count: Dict[str, int] = {n: len(predecessors[n]) for n in sorted_nodes}

    while ready:
        node = ready.pop(0)
        order.append(node)
        for succ in sorted(successors[node]):
            pred_count[succ] -= 1
            if pred_count[succ] == 0:
                # Insert in sorted position to keep deterministic order.
                _insort(ready, succ)

    if len(order) == len(sorted_nodes):
        return order

    # Cycle present: extract the strongly-connected nodes that didn't
    # make it into the topo order, then trace one cycle through them
    # for the error message.
    remaining = [n for n in sorted_nodes if n not in order]
    cycle = _trace_cycle(remaining, successors)
    edge_descs: List[str] = []
    fk_pairs: List[Tuple[str, str, str, str]] = []
    for i in range(len(cycle)):
        src = cycle[i]
        dst = cycle[(i + 1) % len(cycle)]
        descs = edge_provenance.get((src, dst), [f"{src} -> {dst}"])
        edge_descs.extend(descs)
        for record in fk_edge_record.get((src, dst), []):
            fk_pairs.append(record)
    raise MigrationOrderingError(
        cycle_extensions=cycle,
        cycle_tables=fk_pairs,
        edge_descriptions=edge_descs,
    )


def _insort(seq: List[str], item: str) -> None:
    """Insert ``item`` into ``seq`` keeping it sorted (small lists, no bisect dependency)."""
    for i, existing in enumerate(seq):
        if item < existing:
            seq.insert(i, item)
            return
    seq.append(item)


def _trace_cycle(remaining: Sequence[str], successors: Mapping[str, Set[str]]) -> List[str]:
    """Return one cycle through ``remaining`` (DFS-based extraction).

    ``remaining`` is the set of nodes Kahn's algorithm could not flatten
    — every cycle in the original graph lives within this set. We walk
    successors greedily until we revisit a node, then return the slice
    from the first occurrence onward.
    """
    if not remaining:
        return []
    start = sorted(remaining)[0]
    path: List[str] = [start]
    seen: Dict[str, int] = {start: 0}
    current = start
    remaining_set = set(remaining)
    while True:
        next_candidates = sorted(successors.get(current, set()) & remaining_set)
        if not next_candidates:
            # Dead-end inside the remaining set — return what we have.
            return path
        nxt = next_candidates[0]
        if nxt in seen:
            return path[seen[nxt]:]
        seen[nxt] = len(path)
        path.append(nxt)
        current = nxt
