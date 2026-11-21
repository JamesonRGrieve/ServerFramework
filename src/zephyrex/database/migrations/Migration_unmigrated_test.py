# SPDX-License-Identifier: AGPL-3.0-or-later
"""An extension without migrations gets its tables from its models only on a developer's machine
and in tests; anywhere else the boot refuses, naming it."""

import pytest

from zephyrex.database.migrations.Migration import refuse_unmigrated_outside_tests


@pytest.mark.parametrize("environment", ["staging", "production", "development", ""])
def test_outside_local_and_ci_the_boot_refuses(environment: str) -> None:
    with pytest.raises(RuntimeError) as refused:
        refuse_unmigrated_outside_tests(["rpg_table", "ai_chat"], environment)
    assert "ai_chat, rpg_table" in str(refused.value)


@pytest.mark.parametrize("environment", ["local", "ci"])
def test_local_and_ci_keep_the_fallback(environment: str) -> None:
    refuse_unmigrated_outside_tests(["rpg_table"], environment)


def test_nothing_unmigrated_is_never_refused() -> None:
    refuse_unmigrated_outside_tests([], "production")
