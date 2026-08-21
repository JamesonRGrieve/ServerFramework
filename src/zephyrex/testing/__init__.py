# SPDX-License-Identifier: AGPL-3.0-or-later
"""Packaged test infrastructure for zephyrex and its consumer projects.

Consumer projects wire the shared fixtures via their ``conftest.py``::

    pytest_plugins = ["zephyrex.testing.fixtures"]

and import the factory helpers directly::

    from zephyrex.testing import create_user, add_user_to_team

so the framework and every downstream app share one test harness (DRY)
instead of each carrying its own copy of the fixtures/factories.
"""

from zephyrex.testing.factories import (
    add_user_to_team,
    authorize_user,
    bind_test_models,
    create_role,
    create_team,
    create_test_extension_server,
    create_user,
    generate_test_email,
    make_admin_a,
    make_admin_b,
    make_mod_b,
    make_mod_b_role,
    make_team_a,
    make_team_b,
    make_user_b,
)

__all__ = [
    "add_user_to_team",
    "authorize_user",
    "bind_test_models",
    "create_role",
    "create_team",
    "create_test_extension_server",
    "create_user",
    "generate_test_email",
    "make_admin_a",
    "make_admin_b",
    "make_mod_b",
    "make_mod_b_role",
    "make_team_a",
    "make_team_b",
    "make_user_b",
]
