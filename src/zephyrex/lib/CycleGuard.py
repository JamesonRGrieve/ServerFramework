"""Cycle detection for parent-chain trees and DAG dependency edges.

Used by managers that own self-referential or many-to-many graph
relationships (FactionModel.parent_id, LocationModel.parent_id,
HookDependencyModel parent/child) to reject mutations that would close
a cycle.

Two kinds of structures are covered:

1. **Single-parent trees** — each node has at most one parent
   (``parent_id``); the structure is a forest of trees. Use
   ``would_create_tree_cycle`` to check whether assigning a new parent
   to a node would close a cycle.

2. **DAGs over many-to-many edges** — nodes connect via a separate
   edge table (e.g. ``hook_dependencies``), each row a directed edge
   ``parent → child``; multiple parents per node are allowed but
   cycles are rejected. Use ``would_create_dag_cycle`` to check
   whether adding a new edge would close a cycle.

Doctrine: cycle guards are correctness, not granularity. They reject
logical impossibilities (a Location whose ancestor chain is itself; a
Hook that prerequisites itself transitively). They do **not** impose
depth caps — depth is unlimited; only termination is enforced via a
``max_iterations`` safety bound on the walk.

Usage from a manager hook:

.. code-block:: python

    @hook_bll(FactionManager.update, timing=HookTiming.BEFORE)
    def faction_no_cycle(context: HookContext):
        update = context.kwarg("update_data") or context.arg(0)
        if update is None or not getattr(update, "parent_id", None):
            return
        node_id = context.kwarg("id") or context.arg(1)

        def get_parent_id(faction_id: str) -> Optional[str]:
            row = context.manager.get(id=faction_id)
            return getattr(row, "parent_id", None) if row else None

        if would_create_tree_cycle(node_id, update.parent_id, get_parent_id):
            raise CycleGuardError(
                f"Setting parent_id={update.parent_id} on faction "
                f"{node_id} would close a cycle."
            )
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional


class CycleGuardError(Exception):
    """Raised when a mutation would close a cycle, or when a walk
    exceeds ``max_iterations`` (indicating either pre-existing
    corruption or a malicious / extreme structure)."""


def would_create_tree_cycle(
    node_id: str,
    candidate_parent_id: Optional[str],
    get_parent_id: Callable[[str], Optional[str]],
    max_iterations: int = 256,
) -> bool:
    """Return True if setting ``node_id``'s parent to
    ``candidate_parent_id`` would close a cycle.

    Walks up from ``candidate_parent_id`` following ``get_parent_id``;
    if the walk reaches ``node_id``, the new edge would create a cycle.
    Returns False if ``candidate_parent_id`` is None (clearing the
    parent never creates a cycle) or if the walk reaches a root
    (parent_id is None).

    Raises ``CycleGuardError`` if the walk exceeds ``max_iterations``
    (probable pre-existing cycle or corrupted data).
    """
    if candidate_parent_id is None:
        return False
    if candidate_parent_id == node_id:
        return True  # self-parent
    current: Optional[str] = candidate_parent_id
    visited = set()
    iterations = 0
    while current is not None:
        if iterations >= max_iterations:
            raise CycleGuardError(
                f"parent walk from {candidate_parent_id!r} exceeded "
                f"{max_iterations} iterations — probable pre-existing cycle "
                f"or corrupt data."
            )
        if current == node_id:
            return True
        if current in visited:
            # Pre-existing cycle that doesn't touch node_id; not our
            # problem here, but signal so callers can repair.
            raise CycleGuardError(
                f"pre-existing cycle detected in parent chain "
                f"starting at {candidate_parent_id!r}."
            )
        visited.add(current)
        iterations += 1
        current = get_parent_id(current)
    return False


def would_create_dag_cycle(
    new_parent_id: str,
    new_child_id: str,
    get_parents: Callable[[str], Iterable[str]],
    max_iterations: int = 1024,
) -> bool:
    """Return True if adding the directed edge
    ``new_parent_id → new_child_id`` would close a cycle in the DAG.

    Walks the existing-graph ancestors of ``new_parent_id`` via
    ``get_parents`` (which returns the set of immediate parents of a
    node). If the walk encounters ``new_child_id``, the new edge would
    close a cycle (because there's already a path
    ``new_child_id → ... → new_parent_id``, and the new edge would
    extend it back to ``new_child_id``).

    Self-loops (``new_parent_id == new_child_id``) are cycles by
    definition.

    Raises ``CycleGuardError`` if the walk exceeds ``max_iterations``.
    """
    if new_parent_id == new_child_id:
        return True
    visited = {new_parent_id}
    frontier = [new_parent_id]
    iterations = 0
    while frontier:
        if iterations >= max_iterations:
            raise CycleGuardError(
                f"ancestor walk from {new_parent_id!r} exceeded "
                f"{max_iterations} iterations — probable pre-existing cycle "
                f"or corrupt data."
            )
        iterations += 1
        node = frontier.pop()
        for parent in get_parents(node):
            if parent == new_child_id:
                return True
            if parent in visited:
                continue
            visited.add(parent)
            frontier.append(parent)
    return False


def detect_existing_tree_cycle(
    start_id: str,
    get_parent_id: Callable[[str], Optional[str]],
    max_iterations: int = 256,
) -> bool:
    """Return True if walking up from ``start_id`` reaches ``start_id``
    again — i.e. an already-corrupt parent chain. Diagnostic-only."""
    visited = set()
    current: Optional[str] = start_id
    iterations = 0
    while current is not None:
        if iterations >= max_iterations:
            raise CycleGuardError(
                f"parent walk from {start_id!r} exceeded {max_iterations} "
                f"iterations — probable corruption."
            )
        if current in visited:
            return True
        visited.add(current)
        iterations += 1
        current = get_parent_id(current)
    return False


__all__ = [
    "CycleGuardError",
    "would_create_tree_cycle",
    "would_create_dag_cycle",
    "detect_existing_tree_cycle",
]
