# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extension definition for attachments: files (pictures, documents) stored in the
framework's cloud storage and attached to any record by its type and id."""

from typing import Any, ClassVar, Dict

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


class EXT_Attachments(AbstractStaticExtension):
    name: ClassVar[str] = "attachments"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Files stored in cloud storage and attached to any record, read under that record's access."
    )
    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="cloud",
                friendly_name="Cloud storage",
                reason="An attachment's bytes live in the install's object storage",
            ),
        ]
    )
