# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structural contract for RouterMixin-tagged BLL managers (issue #221).

The generation engine (``pydantic2.fastapi`` / ``pydantic2.strawberry``)
introspects a manager *class* to emit REST/GraphQL. It annotated that class as
``Type[logic.AbstractLogicManager.AbstractBLLManager]``, which forced the
generation layer to import upward into ``logic/`` — the last remaining
``lib/ -> logic/`` coupling issue #221 set out to remove.

``ManagerContract`` is a :class:`typing.Protocol` capturing exactly the
class-level surface the engine reads off a manager class (``BaseModel`` /
``Model`` / ``Router`` / ``example_overrides`` / ``register``) plus
construction. Every ``AbstractBLLManager`` subclass satisfies it *structurally*
— no inheritance, no import — so the generation layer annotates with
``Type[ManagerContract]`` and no longer reaches into ``logic/``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol


class ManagerContract(Protocol):
    """The manager-class surface consumed by the generation engine."""

    BaseModel: ClassVar[Any]
    Model: ClassVar[Any]
    Router: ClassVar[Any]
    example_overrides: ClassVar[Any]

    def register(self, *args: Any, **kwargs: Any) -> Any: ...

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
