# SPDX-License-Identifier: AGPL-3.0-or-later
"""An extension's required dependency loads like a listed extension: its
models are bound and its tables exist, not just its name. The registry
resolved required dependencies (``loaded_extension_names`` named them) while
model binding, model discovery and pip installs read only the configured
list, so a dependency was "loaded" with none of its models: VTT's app, which
pulled in metadata as a dependency, failed every start with "No matching type
found in registry for MetadataModel!"."""

import pytest
from sqlalchemy import inspect as sa_inspect

# (listed extension, a required dependency it doesn't list, a model of it)
CASES = [
    ("entitlements", "payment", "PaymentModel"),
    ("conversations", "acl_rbac", "PermissionModel"),
]


@pytest.mark.parametrize("listed, dependency, model_name", CASES)
def test_a_required_dependency_is_loaded_whole(
    listed, dependency, model_name, tmp_path
):
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    with pytest.MonkeyPatch.context() as env_patch:
        env_patch.setenv("DATABASE_PATH", str(tmp_path))
        prepare_test_registry()
        app = instance(db_prefix=f"test.depboot.{listed}", extensions=listed)

    registry = app.state.model_registry
    assert dependency in registry.loaded_extension_names()
    bound = {model.__name__ for model in registry.bound_models}
    assert model_name in bound, f"{dependency}'s {model_name} is not bound"

    db_manager = registry.DB.manager
    in_database = set(sa_inspect(db_manager._setup_engine).get_table_names())
    owned = {
        name
        for name, table in db_manager.Base.metadata.tables.items()
        if table.info.get("extension") == dependency
    }
    assert owned, f"no tables of {dependency} are defined"
    assert owned <= in_database, f"missing tables: {sorted(owned - in_database)}"


def test_an_optional_dependency_is_not_loaded(tmp_path):
    """ai_prompts optionally extends conversations' messages. Alone, it binds
    nothing of conversations: it once bound MessageModel from its own module
    namespace, whose foreign key to conversations then stopped the app from
    starting."""
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    with pytest.MonkeyPatch.context() as env_patch:
        env_patch.setenv("DATABASE_PATH", str(tmp_path))
        prepare_test_registry()
        app = instance(db_prefix="test.depboot.prompts_alone", extensions="ai_prompts")

    registry = app.state.model_registry
    assert "conversations" not in registry.loaded_extension_names()
    assert "MessageModel" not in {m.__name__ for m in registry.bound_models}
