"""Backward-compatible shim.

The test factory helpers now live in the packaged ``zephyrex.testing.factories``
module so consumer projects can share them (DRY). This module re-exports them
so the framework's own ``from conftest_factories import ...`` sites keep working.
"""

from zephyrex.testing.factories import *  # noqa: F401,F403
from zephyrex.testing.factories import (  # noqa: F401
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
