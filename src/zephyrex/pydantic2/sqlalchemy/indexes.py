# SPDX-License-Identifier: AGPL-3.0-or-later
"""Indexes a Pydantic model declares for the table built from it.

A model lists them as ``table_indexes: ClassVar[Tuple[TableIndex, ...]]``;
the builder adds one ``Index`` per declaration to every table it builds for
the model (an ``Index`` belongs to exactly one table, and each app's
declarative base builds its own). The table's migration creates the index;
the declaration keeps the model and the migrated schema in step.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import Index, text


    """An index on ``columns``; ``where`` (a SQL predicate) makes it
    partial, on SQLite and PostgreSQL alike."""

    name: str
    columns: Tuple[str, ...]
    unique: bool = False
    where: Optional[str] = None

        dialect_options: Dict[str, Any] = {}
        if self.where is not None:
            dialect_options = {
            }
        return Index(self.name, *self.columns, unique=self.unique, **dialect_options)
