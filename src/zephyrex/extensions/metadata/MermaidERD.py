# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render the committed SQLAlchemy metadata as a Mermaid ``erDiagram``.

Introspects ``model_registry.declarative_base.metadata`` — every table, its
columns (type, primary/foreign key), and the ``comment=`` metadata the framework
already carries on tables and columns (``database/AbstractDatabaseEntity`` sets
these; ``lib/Pydantic2SQLAlchemy`` folds Pydantic field descriptions into column
comments) — and emits a Mermaid entity-relationship diagram. Column comments are
rendered inline (Mermaid shows them on the attribute row); foreign keys become
relationship edges. Intended for a dev-only endpoint (see the ``/erd`` route,
gated the same way as ``/openapi.json``).
"""

import re
from typing import Any

# Mermaid entity/attribute identifiers and the relationship label must be bare
# tokens (no spaces/punctuation); the type slot and the quoted comment are freer
# but must not contain a double-quote or newline.
_IDENT_RE = re.compile(r"[^0-9A-Za-z_]+")


def _ident(name: Any) -> str:
    """Sanitize a name into a Mermaid-safe identifier."""
    token = _IDENT_RE.sub("_", str(name)).strip("_")
    return token or "unnamed"


def _type_token(sa_type: Any) -> str:
    """A compact, Mermaid-safe token for a SQLAlchemy column type."""
    return _ident(str(sa_type).split("(")[0]) or "value"


def _comment(text: Any) -> str:
    """Escape a comment for a Mermaid ``"..."`` slot (drop quotes/newlines)."""
    if not text:
        return ""
    return " ".join(str(text).replace('"', "'").split())


def build_mermaid_erd(model_registry: Any) -> str:
    """Return a Mermaid ``erDiagram`` for every entity in the registry.

    Returns a diagram with no entities (``"erDiagram\\n"``) if the registry has
    not been committed / has no declarative base yet, so callers always get valid
    Mermaid.
    """
    base = getattr(model_registry, "declarative_base", None)
    metadata = getattr(base, "metadata", None) if base is not None else None
    if metadata is None:
        return "erDiagram\n"

    tables = list(getattr(metadata, "sorted_tables", metadata.tables.values()))
    lines = ["erDiagram"]

    for table in tables:
        entity = _ident(table.name)
        table_comment = _comment(getattr(table, "comment", None))
        if table_comment:
            lines.append(f"    %% {entity}: {table_comment}")
        lines.append(f"    {entity} {{")
        for col in table.columns:
            attr = [_type_token(col.type), _ident(col.name)]
            if col.primary_key:
                attr.append("PK")
            elif col.foreign_keys:
                attr.append("FK")
            row = "        " + " ".join(attr)
            col_comment = _comment(getattr(col, "comment", None))
            if col_comment:
                row += f' "{col_comment}"'
            lines.append(row)
        lines.append("    }")

    # Foreign keys → relationship edges (parent ||--o{ child : "fk column").
    for table in tables:
        child = _ident(table.name)
        for fk in sorted(table.foreign_keys, key=lambda f: str(f.parent.name)):
            parent = _ident(fk.column.table.name)
            label = _ident(fk.parent.name)
            lines.append(f'    {parent} ||--o{{ {child} : "{label}"')

    return "\n".join(lines) + "\n"
