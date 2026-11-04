# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structural contract for the BLL managers the generation engine drives.

The generation engine (``pydantic2.fastapi`` / ``pydantic2.strawberry``)
introspects a manager *class* to emit REST/GraphQL and calls its CRUD methods.
Annotating that as ``Type[logic.AbstractLogicManager.AbstractBLLManager]``
would make the generation layer import upward into ``logic/``, so the engine
annotates with these protocols instead; every ``AbstractBLLManager`` subclass
satisfies ``ManagerContract`` structurally, with no inheritance or import.

Capabilities only some managers have are separate: routing comes from
``RouterMixin`` (checked with ``issubclass``), and ``SelfScopedManagerContract``
marks the one entity that is the requester itself.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol


class ManagerContract(Protocol):
    """The manager surface consumed by the generation engine."""

    Model: Any
    example_overrides: ClassVar[Any]

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def create(self, *args: Any, **kwargs: Any) -> Any: ...

    def get(self, *args: Any, **kwargs: Any) -> Any: ...

    def list(self, *args: Any, **kwargs: Any) -> Any: ...

    def search(self, *args: Any, **kwargs: Any) -> Any: ...

    def update(self, *args: Any, **kwargs: Any) -> Any: ...

    def delete(self, *args: Any, **kwargs: Any) -> Any: ...

    def batch_update(self, *args: Any, **kwargs: Any) -> Any: ...

    def batch_delete(self, *args: Any, **kwargs: Any) -> Any: ...


class SelfScopedManagerContract(ManagerContract, Protocol):
    """A manager whose entity is the requester (the User): it is created by
    registration, and updates and deletes act on the requester, not an id."""

    @staticmethod
    def register(*args: Any, **kwargs: Any) -> Any: ...
