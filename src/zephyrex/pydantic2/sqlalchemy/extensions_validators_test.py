# SPDX-License-Identifier: AGPL-3.0-or-later
"""An @extension_model's validators reach the model it extends (found by zephyrex-rpg-wiki: its
Person kind and entry checks never ran, so a bad value reached the database). A field_validator
and a model_validator on the extension's model, Create and Update refuse bad input once applied,
good input still passes, the target's own validators are kept, and a reset restores them.
"""

from collections.abc import Iterator
from typing import Any, Optional

import pytest
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from zephyrex.pydantic2.sqlalchemy.extensions import (
    _apply_model_extension,
    reset_extension_system,
)

KINDS = {"human", "elf"}


def _kind(value: Optional[str]) -> Optional[str]:
    if value is not None and value not in KINDS:
        raise ValueError("kind is human or elf")
    return value


def _not_an_elf_named_bob(model: Any) -> Any:
    if getattr(model, "kind", None) == "elf" and getattr(model, "name", None) == "Bob":
        raise ValueError("no elf is named Bob")
    return model


def _target() -> type:
    class PersonModel(BaseModel):
        name: str

        class Create(BaseModel):
            name: str

            # The target's own check, named as the extension's is: both must run.
            @field_validator("name")
            @classmethod
            def _checked(cls, value: str) -> str:
                if not value.strip():
                    raise ValueError("a name is required")
                return value.strip()

        class Update(BaseModel):
            name: Optional[str] = None

    return PersonModel


def _extension() -> type:
    class Kind_PersonModel(BaseModel):
        kind: Optional[str] = None

        @field_validator("kind")
        @classmethod
        def _checked(cls, value: Optional[str]) -> Optional[str]:
            return _kind(value)

        class Create(BaseModel):
            kind: Optional[str] = None

            @field_validator("kind")
            @classmethod
            def _checked(cls, value: Optional[str]) -> Optional[str]:
                return _kind(value)

            @model_validator(mode="after")
            def _whole(self) -> Any:
                return _not_an_elf_named_bob(self)

        class Update(BaseModel):
            kind: Optional[str] = None

            @field_validator("kind")
            @classmethod
            def _checked(cls, value: Optional[str]) -> Optional[str]:
                return _kind(value)

            @model_validator(mode="after")
            def _whole(self) -> Any:
                return _not_an_elf_named_bob(self)

    return Kind_PersonModel


@pytest.fixture
def person() -> Iterator[Any]:
    target = _target()
    _apply_model_extension(target, _extension())
    yield target
    reset_extension_system()


def test_create_refuses_what_the_extension_refuses(person: Any) -> None:
    assert person.Create(name="Ada", kind="elf").kind == "elf"
    with pytest.raises(ValidationError, match="kind is human or elf"):
        person.Create(name="Ada", kind="dragon")
    with pytest.raises(ValidationError, match="no elf is named Bob"):
        person.Create(name="Bob", kind="elf")
    # The target's own validator still runs.
    assert person.Create(name="  Ada ", kind="human").name == "Ada"
    with pytest.raises(ValidationError, match="a name is required"):
        person.Create(name="  ", kind="human")


def test_update_refuses_what_the_extension_refuses(person: Any) -> None:
    assert person.Update(kind="human").kind == "human"
    assert person.Update(name="Ada").kind is None
    with pytest.raises(ValidationError, match="kind is human or elf"):
        person.Update(kind="dragon")
    with pytest.raises(ValidationError, match="no elf is named Bob"):
        person.Update(name="Bob", kind="elf")


def test_the_model_itself_refuses_it(person: Any) -> None:
    assert person(name="Ada", kind="human").kind == "human"
    with pytest.raises(ValidationError, match="kind is human or elf"):
        person(name="Ada", kind="dragon")


def test_a_reset_restores_the_targets_own_validators() -> None:
    target = _target()
    _apply_model_extension(target, _extension())
    reset_extension_system()
    # The extension's check is gone; the target's own is still there.
    target.Create(name="Ada", kind="dragon")
    with pytest.raises(ValidationError, match="a name is required"):
        target.Create(name=" ")
