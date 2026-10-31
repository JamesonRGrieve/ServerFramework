# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entitlements: which features and limits a user's plan gives them.

An app registers its plans (``Plans.register_plan``); a deployment maps
its payment provider's plans onto them (``ENTITLEMENT_<PLAN>_PLAN_IDS``).
A user's plan comes from their active payment subscriptions and the seats
subscribers share with them. ``require_feature`` and
``require_within_limit`` refuse what the plan does not cover with 402,
naming the plans that would; the client reads ``GET /v1/entitlement`` to
show what is unlocked and offer an upgrade for the rest.
"""

from typing import Any, ClassVar, Dict, List

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


class EXT_Entitlements(AbstractStaticExtension):
    name: ClassVar[str] = "entitlements"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Plans, features, limits and shared seats, from payment subscriptions"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="payment",
                friendly_name="Payments",
                reason="A user's subscriptions say which plan they pay for",
            ),
        ]
    )
    _providers: ClassVar[List[Any]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        return True
