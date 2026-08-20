# SPDX-License-Identifier: AGPL-3.0-or-later
"""MariaDB database provider (Provider Rotation System, static).

MariaDB speaks the MySQL wire protocol and uses the same ``mysql.connector``
driver, so this provider is a thin metadata override of :class:`PRV_MySQL`.
"""

from zephyrex.extensions.database.PRV_MySQL import PRV_MySQL


class PRV_MariaDB(PRV_MySQL):
    """MariaDB database provider (static, rotation-compatible)."""

    name: str = "MariaDB"
    friendly_name: str = "MariaDB Database"
    description: str = "MariaDB relational database provider"
    db_type: str = "mariadb"
