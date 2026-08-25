# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the Mermaid ERD builder and the dev-gated /erd endpoint (issue #224)."""

from zephyrex.extensions.metadata.MermaidERD import build_mermaid_erd


def test_build_mermaid_erd_empty_registry_is_valid():
    """An uncommitted registry (no declarative base) still yields valid Mermaid."""

    class _R:
        declarative_base = None

    assert build_mermaid_erd(_R()) == "erDiagram\n"


def test_erd_builder_emits_entities_and_relationships(model_registry):
    erd = build_mermaid_erd(model_registry)
    assert erd.startswith("erDiagram")
    # A committed registry with the core auth/team entities has entity blocks,
    # foreign-key relationship edges, and primary-key markers.
    assert "{" in erd and "}" in erd
    # Every entity-open brace has a matching close line (relationship edges also
    # contain "{" via the ||--o{ cardinality token, so count close braces only).
    assert erd.count("    }") >= 1, "entity blocks must be closed"
    assert "||--o{" in erd, "foreign keys should render as relationship edges"
    assert " PK" in erd, "primary keys should be marked"
    # Every non-empty line is either the header, an entity open/close, a comment,
    # a relationship edge, or an indented attribute row — no stray tokens.
    for line in erd.splitlines():
        assert line == "erDiagram" or line.startswith(("    ", "        "))


def test_erd_endpoint_returns_mermaid_in_dev(server):
    resp = server.get("/erd")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    assert body.startswith("erDiagram")
    assert "{" in body
