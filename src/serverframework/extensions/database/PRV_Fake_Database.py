"""Fake database provider for offline-CI testing (Item 72).

A minimal real provider that satisfies the
``AbstractDatabaseExtensionProvider`` contract without any external
dependencies. Tests that previously patched
``EXT_Database.root.rotate(...)`` use this provider directly to exercise
the provider methods rather than mocking the rotation system.

Per Item 15 / Item 72, this provider is **not a mock** — it is a real
class with deterministic in-memory state, instantiated and called like
any other provider. The framework's "no mocks for BLL/extension surface"
pillar holds.
"""

from __future__ import annotations

from typing import Any, Dict, List

from serverframework.extensions.database.EXT_Database import (
    AbstractDatabaseExtensionProvider,
    EXT_Database,
)


class PRV_Fake_Database(AbstractDatabaseExtensionProvider):
    """In-memory fake database provider for tests.

    Returns deterministic strings from each method so tests can assert
    against known values. Records calls to ``executed_queries`` so tests
    can verify the rotation system reached the provider.
    """

    name = "fake_database"
    friendly_name = "Fake Database (Test)"
    description = "Deterministic in-memory provider used by offline-CI tests."
    db_type = "sqlite"  # Use sqlite for relational classification

    _env: Dict[str, Any] = {}
    _abilities = {
        "database",
        "sql",
        "data_storage",
    }

    extension = EXT_Database

    # Class-level state for tests to inspect.
    executed_queries: List[str] = []
    last_request: str = ""
    last_data: str = ""

    @classmethod
    def bond_instance(cls, config: Dict[str, Any]) -> None:
        """No-op bond_instance — the fake provider has no real backend."""
        cls._config = dict(config)

    @classmethod
    async def execute_sql(cls, query: str, **kwargs) -> str:
        cls.executed_queries.append(query)
        return f"fake-sql:{query}"

    @classmethod
    async def get_schema(cls, **kwargs) -> str:
        return (
            "CREATE TABLE fake_table (id INTEGER PRIMARY KEY, value TEXT);"
        )

    @classmethod
    async def chat_with_db(cls, request: str, **kwargs) -> str:
        cls.last_request = request
        return f"fake-chat:{request}"

    @classmethod
    async def execute_query(cls, query: str, **kwargs) -> str:
        cls.executed_queries.append(query)
        return f"fake-query:{query}"

    @classmethod
    async def write_data(cls, data: str, **kwargs) -> str:
        cls.last_data = data
        return f"fake-write:{data}"

    @classmethod
    def validate_config(cls) -> List[str]:
        return []  # No issues — fake provider is always configured

    @classmethod
    def reset(cls) -> None:
        """Reset captured state. Tests should call this in setup/teardown."""
        cls.executed_queries = []
        cls.last_request = ""
        cls.last_data = ""
