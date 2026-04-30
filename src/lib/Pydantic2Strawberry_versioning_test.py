"""Tests for the GraphQL deprecation/sunset directive integration (Item 39).

These tests exercise the helpers in :mod:`lib.Pydantic2Strawberry` that read
``RouterMixin.deprecated_in`` / ``sunset_in`` ClassVars and translate them
into Strawberry ``deprecation_reason`` arguments and ``@sunset`` schema
directives.

No mocks: the tests use real ``strawberry`` field machinery and a real
``RouterMixin`` subclass to drive the helpers.
"""

from __future__ import annotations

import strawberry

from lib.Pydantic2FastAPI import RouterMixin
from lib.Pydantic2Strawberry import (
    Sunset,
    _build_field_kwargs,
    _versioned_field,
    _versioning_metadata_for,
)


# -- Manager fixtures ---------------------------------------------------------


class _PristineManager(RouterMixin):
    """No deprecation metadata — fields should be unaltered."""

    prefix = "/v1/pristine"


class _DeprecatedOnlyManager(RouterMixin):
    """Only ``deprecated_in`` is set — no sunset directive expected."""

    prefix = "/v1/deprecated_only"
    deprecated_in = "2026-01-01"


class _DeprecatedAndSunsetManager(RouterMixin):
    """Both ``deprecated_in`` and ``sunset_in`` set — full metadata."""

    prefix = "/v1/full"
    deprecated_in = "2026-01-01"
    sunset_in = "2026-07-01"


class _SunsetOnlyManager(RouterMixin):
    """``sunset_in`` set without ``deprecated_in`` — directive only."""

    prefix = "/v1/sunset_only"
    sunset_in = "2026-07-01"


# -- Metadata extraction ------------------------------------------------------


def test_versioning_metadata_for_pristine_manager():
    deprecated_in, sunset_in = _versioning_metadata_for(_PristineManager)
    assert deprecated_in is None
    assert sunset_in is None


def test_versioning_metadata_for_deprecated_only():
    deprecated_in, sunset_in = _versioning_metadata_for(_DeprecatedOnlyManager)
    assert deprecated_in == "2026-01-01"
    assert sunset_in is None


def test_versioning_metadata_for_deprecated_and_sunset():
    deprecated_in, sunset_in = _versioning_metadata_for(
        _DeprecatedAndSunsetManager
    )
    assert deprecated_in == "2026-01-01"
    assert sunset_in == "2026-07-01"


def test_versioning_metadata_for_none_returns_pair_of_none():
    assert _versioning_metadata_for(None) == (None, None)


# -- Field-kwargs construction -----------------------------------------------


def test_build_field_kwargs_pristine_returns_empty():
    assert _build_field_kwargs(_PristineManager) == {}


def test_build_field_kwargs_deprecated_only_carries_reason_no_directive():
    kwargs = _build_field_kwargs(_DeprecatedOnlyManager)
    assert "deprecation_reason" in kwargs
    assert "2026-01-01" in kwargs["deprecation_reason"]
    # Without sunset_in there should be no directive entry.
    assert "directives" not in kwargs


def test_build_field_kwargs_full_carries_reason_and_sunset_directive():
    kwargs = _build_field_kwargs(_DeprecatedAndSunsetManager)
    assert "deprecation_reason" in kwargs
    reason = kwargs["deprecation_reason"]
    assert "2026-01-01" in reason
    assert "2026-07-01" in reason
    assert "directives" in kwargs
    directives = kwargs["directives"]
    assert len(directives) == 1
    sunset_directive = directives[0]
    assert isinstance(sunset_directive, Sunset)
    assert sunset_directive.date == "2026-07-01"


def test_build_field_kwargs_sunset_only_emits_directive_without_reason():
    kwargs = _build_field_kwargs(_SunsetOnlyManager)
    assert "deprecation_reason" not in kwargs
    assert "directives" in kwargs
    assert isinstance(kwargs["directives"][0], Sunset)
    assert kwargs["directives"][0].date == "2026-07-01"


# -- Field assembly via ``_versioned_field`` ---------------------------------


def _resolver() -> str:
    return "ok"


def test_versioned_field_pristine_produces_plain_strawberry_field():
    field = _versioned_field(_resolver, _PristineManager)
    # No deprecation_reason should be set.
    assert getattr(field, "deprecation_reason", None) is None


def test_versioned_field_deprecated_only_sets_reason():
    field = _versioned_field(_resolver, _DeprecatedOnlyManager)
    assert field.deprecation_reason
    assert "2026-01-01" in field.deprecation_reason


def test_versioned_field_full_sets_reason_and_directive():
    field = _versioned_field(_resolver, _DeprecatedAndSunsetManager)
    assert field.deprecation_reason
    assert "2026-01-01" in field.deprecation_reason
    assert "2026-07-01" in field.deprecation_reason
    # The Sunset directive should be attached to the field.
    directives = list(field.directives or [])
    assert any(isinstance(d, Sunset) for d in directives)
    sunset = next(d for d in directives if isinstance(d, Sunset))
    assert sunset.date == "2026-07-01"


def test_versioned_field_no_manager_class_returns_plain_field():
    """Robustness: a ``None`` manager_class should not crash."""
    field = _versioned_field(_resolver, None)
    assert getattr(field, "deprecation_reason", None) is None


# -- Sunset directive smoke ---------------------------------------------------


def test_sunset_directive_is_a_strawberry_schema_directive():
    """The ``Sunset`` helper should be a real Strawberry schema directive.

    We don't import private internals; instead we verify the dataclass-ish
    surface (the ``date`` field) and the fact that ``Sunset(date=...)``
    instantiates cleanly so it can be passed via ``directives=[...]``.
    """
    instance = Sunset(date="2026-07-01")
    assert instance.date == "2026-07-01"


def test_sunset_directive_round_trips_through_strawberry_field():
    """Wiring the Sunset directive on a field via strawberry.field works."""
    field = strawberry.field(_resolver, directives=[Sunset(date="2026-07-01")])
    directives = list(field.directives or [])
    assert any(isinstance(d, Sunset) for d in directives)
