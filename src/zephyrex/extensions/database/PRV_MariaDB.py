# SPDX-License-Identifier: AGPL-3.0-or-later
"""MariaDB database provider (Provider Rotation System, static).

MariaDB speaks the MySQL wire protocol and uses the same ``mysql.connector``
driver, so this provider is a thin metadata override of :class:`PRV_MySQL`.
"""

from typing import ClassVar

from zephyrex.extensions.database.PRV_MySQL import PRV_MySQL


class PRV_MariaDB(PRV_MySQL):
    """MariaDB database provider (static, rotation-compatible)."""

    name: ClassVar[str] = "MariaDB"
    friendly_name: ClassVar[str] = "MariaDB Database"
    description: ClassVar[str] = "MariaDB relational database provider"
    db_type: ClassVar[str] = "mariadb"
