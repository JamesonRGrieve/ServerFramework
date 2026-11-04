# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared low-level pydantic2 conventions (no intra-package dependencies).

The framework convention that a ``<name>_id`` field is a foreign-key reference
to the ``<Name>Model`` model is a single piece of knowledge that both the
SQLAlchemy builder and the Strawberry emitter act on. Centralizing it here is the
single source of truth for "what is a reference field", so the two transports
cannot drift into different notions (a relationship present in REST but not
GraphQL, or vice versa) -- issue #225.
"""

from __future__ import annotations

import re

import stringcase

#: The primary-key column, which is NOT a reference to another model.
PRIMARY_KEY_FIELD = "id"
_REFERENCE_SUFFIX = "_id"


def is_reference_field_name(name: str) -> bool:
    """Whether a field name denotes a foreign-key reference to another model.

    A ``<name>_id`` field references the ``<Name>Model`` model; the bare primary
    key ``id`` is excluded (it is the row's own identity, not a reference).
    Note ``"id".endswith("_id")`` is already ``False``, so this matches the bare
    ``endswith("_id")`` checks it replaces while stating the intent explicitly.
    """
    return name.endswith(_REFERENCE_SUFFIX) and name != PRIMARY_KEY_FIELD


def reference_relationship_name(name: str) -> str:
    """The relationship (attribute) name for a ``<name>_id`` reference field.

    ``created_by_user_id`` -> ``created_by_user``. Confirm
    :func:`is_reference_field_name` first.
    """
    return name.removesuffix(_REFERENCE_SUFFIX)


def reference_target_model_name(name: str) -> str:
    """The target model class name for a ``<name>_id`` reference field.

    ``team_id`` -> ``TeamModel``. Confirm :func:`is_reference_field_name` first.
    """
    return f"{stringcase.pascalcase(reference_relationship_name(name))}Model"


# A word boundary inside a class name: lower/digit then upper ("MagicLink"),
# or the last capital of an acronym run before a capitalized word ("APIKey").
_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def wire_resource_name(class_name: str, suffix: str) -> str:
    """The snake_case name a class carries on the wire (REST envelope keys,
    GraphQL custom-route fields), with ``suffix`` ("Manager"/"Model") removed.

    Acronyms stay whole: ``APIKey`` -> ``api_key`` (``stringcase`` gives
    ``a_p_i_key``). Table and foreign-key column names keep the
    ``stringcase`` convention; changing those is a schema migration.
    """
    return _WORD_BOUNDARY.sub("_", class_name.removesuffix(suffix)).lower()


# "Manager" ends a manager's name, or precedes its version token
# (``WidgetManagerV2`` serves ``widget_v2``).
_MANAGER_SUFFIX = re.compile(r"Manager(?=(V\d+)?$)")


def manager_resource_name(manager_cls: type) -> str:
    """The resource a manager serves: ``MagicLinkManager`` -> ``magic_link``."""
    return wire_resource_name(_MANAGER_SUFFIX.sub("", manager_cls.__name__), "")
