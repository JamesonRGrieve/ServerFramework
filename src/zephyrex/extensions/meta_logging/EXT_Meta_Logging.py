# SPDX-License-Identifier: AGPL-3.0-or-later
"""meta_logging extension definition.

Contributes the ``audit_logs`` and ``system_logs`` tables and their managers
(``BLL_Meta_Logging``). It has no lifecycle work: the managers' hooks are bound
to its own classes when ``BLL_Meta_Logging`` is imported.
"""

from typing import ClassVar

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension


class EXT_Meta_Logging(AbstractStaticExtension):
    name: ClassVar[str] = "meta_logging"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Durable audit and system log records"
