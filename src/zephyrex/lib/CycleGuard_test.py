"""Tests for the CycleGuard utility — pure-algorithm coverage."""

from __future__ import annotations

import pytest

from zephyrex.lib.CycleGuard import (
    CycleGuardError,
    detect_existing_tree_cycle,
    would_create_dag_cycle,
    would_create_tree_cycle,
)


# ---------------------------------------------------------------------------
# Tree cycles (single-parent / parent_id chains)
# ---------------------------------------------------------------------------


class TestTreeCycle:
    def test_no_parent_never_creates_cycle(self):
        # Clearing the parent (None) never creates a cycle.
        assert (
            would_create_tree_cycle(
                "leaf", None, get_parent_id=lambda _: None
            )
            is False
        )

    def test_self_parent_is_cycle(self):
        assert (
            would_create_tree_cycle(
                "x", "x", get_parent_id=lambda _: None
            )
            is True
        )

    def test_unrelated_chain_is_not_a_cycle(self):
        # candidate parent is in a separate chain that terminates.
        parents = {"b": "a", "a": None}
        assert (
            would_create_tree_cycle(
                "leaf", "b", get_parent_id=lambda x: parents.get(x)
            )
            is False
        )

    def test_setting_parent_to_descendant_creates_cycle(self):
        # node X has child Y has child Z. Setting X.parent = Z would
        # close a cycle.
        parents = {"y": "x", "z": "y"}
        assert (
            would_create_tree_cycle(
                "x", "z", get_parent_id=lambda i: parents.get(i)
            )
            is True
        )

    def test_setting_parent_to_grandparent_is_not_a_cycle(self):
        # node X is a child of Y is a child of Z. Setting X.parent = Z
        # is just a flatter tree — not a cycle.
        parents = {"y": "z"}
        assert (
            would_create_tree_cycle(
                "x", "z", get_parent_id=lambda i: parents.get(i)
            )
            is False
        )

    def test_pre_existing_cycle_raises(self):
        # candidate parent chain loops on itself, not touching node_id.
        # The guard should refuse rather than spin.
        parents = {"a": "b", "b": "a"}
        with pytest.raises(CycleGuardError, match="pre-existing cycle"):
            would_create_tree_cycle(
                "leaf", "a", get_parent_id=lambda i: parents.get(i)
            )

    def test_max_iterations_caps_runaway_walks(self):
        # An infinite synthetic chain bounded by max_iterations.
        def get_parent(i: str) -> str:
            return f"p{int(i[1:]) + 1}"

        with pytest.raises(CycleGuardError, match="exceeded"):
            would_create_tree_cycle(
                "x", "p0", get_parent_id=get_parent, max_iterations=10
            )

    def test_unlimited_depth_within_iteration_budget(self):
        # 100-deep tree resolves cleanly with the default 256 budget.
        parents = {f"n{i}": f"n{i + 1}" for i in range(100)}
        assert (
            would_create_tree_cycle(
                "leaf", "n0", get_parent_id=lambda i: parents.get(i)
            )
            is False
        )


# ---------------------------------------------------------------------------
# DAG cycles (many-to-many edges, e.g. hook dependencies)
# ---------------------------------------------------------------------------


class TestDAGCycle:
    def test_self_loop_is_cycle(self):
        assert (
            would_create_dag_cycle("h", "h", get_parents=lambda _: [])
            is True
        )

    def test_disconnected_nodes_no_cycle(self):
        # No existing edges; new edge can't close a cycle.
        assert (
            would_create_dag_cycle(
                "parent", "child", get_parents=lambda _: []
            )
            is False
        )

    def test_simple_chain_no_cycle(self):
        # Existing: A → B. Adding B → C is fine.
        parents = {"b": ["a"]}
        assert (
            would_create_dag_cycle(
                "b", "c", get_parents=lambda i: parents.get(i, [])
            )
            is False
        )

    def test_back_edge_creates_cycle(self):
        # Existing: A → B → C. Adding C → A would close A→B→C→A.
        # Walking ancestors of new_parent=C: parents(C)=[B], parents(B)=[A].
        # We hit A which is the new_child_id → cycle.
        parents = {"c": ["b"], "b": ["a"]}
        assert (
            would_create_dag_cycle(
                "c", "a", get_parents=lambda i: parents.get(i, [])
            )
            is True
        )

    def test_diamond_pattern_no_cycle(self):
        # Existing: A → B, A → C, B → D, C → D.
        # Adding D → E is fine.
        parents = {
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        assert (
            would_create_dag_cycle(
                "d", "e", get_parents=lambda i: parents.get(i, [])
            )
            is False
        )

    def test_multi_parent_back_edge_detected(self):
        # Existing: A → B, X → B, B → C.
        # Adding C → A: ancestors of C include B; ancestors of B
        # include A and X. Hits A → cycle.
        parents = {
            "b": ["a", "x"],
            "c": ["b"],
        }
        assert (
            would_create_dag_cycle(
                "c", "a", get_parents=lambda i: parents.get(i, [])
            )
            is True
        )

    def test_max_iterations_cap(self):
        # Pathological get_parents that always yields a fresh ancestor.
        counter = {"n": 0}

        def get_parents(_: str):
            counter["n"] += 1
            return [f"p{counter['n']}"]

        with pytest.raises(CycleGuardError, match="exceeded"):
            would_create_dag_cycle(
                "p", "child", get_parents=get_parents, max_iterations=10
            )


# ---------------------------------------------------------------------------
# Diagnostic: detect_existing_tree_cycle
# ---------------------------------------------------------------------------


class TestDetectExisting:
    def test_clean_chain_no_cycle(self):
        parents = {"b": "a", "a": None}
        assert (
            detect_existing_tree_cycle(
                "b", get_parent_id=lambda i: parents.get(i)
            )
            is False
        )

    def test_corrupted_self_loop(self):
        parents = {"a": "a"}
        assert (
            detect_existing_tree_cycle(
                "a", get_parent_id=lambda i: parents.get(i)
            )
            is True
        )

    def test_corrupted_two_node_loop(self):
        parents = {"a": "b", "b": "a"}
        assert (
            detect_existing_tree_cycle(
                "a", get_parent_id=lambda i: parents.get(i)
            )
            is True
        )
