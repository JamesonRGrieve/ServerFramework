# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan a class's functions without evaluating its attributes.

``inspect.getmembers(cls)`` reads every attribute through ``getattr``, which
invokes descriptors: a classproperty runs its body. Framework classes carry
classproperties that build managers and query the database, so a scan for
decorated methods could depend on whatever registry happened to be
attached. These helpers read attributes statically instead.
"""

from inspect import getmembers_static, isfunction
from types import FunctionType
from typing import Any, List, Tuple


def static_or_plain_functions(cls: type) -> List[Tuple[str, FunctionType]]:
    """``(name, function)`` for each plain function or staticmethod on
    ``cls``, inherited ones included; classmethods and descriptors are not
    functions and are skipped."""
    found: List[Tuple[str, FunctionType]] = []
    for name, member in getmembers_static(cls):
        function = member.__func__ if isinstance(member, staticmethod) else member
        if isfunction(function):
            found.append((name, function))
    return found


def decorated_functions(cls: type, marker: str) -> List[Tuple[str, FunctionType, Any]]:
    """``(name, function, marker value)`` for the functions of
    :func:`static_or_plain_functions` carrying ``marker`` (e.g.
    ``_ability_info``, ``_hook_info``, ``_static_route_config``)."""
    return [
        (name, function, getattr(function, marker))
        for name, function in static_or_plain_functions(cls)
        if hasattr(function, marker)
    ]


def instance_methods(cls: type) -> List[Tuple[str, FunctionType]]:
    """``(name, function)`` for each plain function on ``cls`` (instance
    methods), inherited ones included; static and class methods excluded."""
    return [
        (name, member) for name, member in getmembers_static(cls) if isfunction(member)
    ]
