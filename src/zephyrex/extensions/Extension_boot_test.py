# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every bundled extension boots on its own and gets every table its models
define. Extensions without migrations used to boot with none of their tables
(the create_all fallback ran before the models existed), and several could not
boot alone at all; nothing noticed because nothing booted them individually."""

from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect

EXTENSIONS_DIR = Path(__file__).resolve().parent
BUNDLED_EXTENSIONS = sorted(
    {
        path.parent.name
        for path in EXTENSIONS_DIR.glob("*/EXT_*.py")
        if not path.name.endswith("_test.py")
    }
)


@pytest.mark.parametrize("extension", BUNDLED_EXTENSIONS)
def test_extension_boots_alone_with_all_its_tables(extension, tmp_path):
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    with pytest.MonkeyPatch.context() as env_patch:
        env_patch.setenv("DATABASE_PATH", str(tmp_path))
        prepare_test_registry()
        app = instance(db_prefix=f"test.boot.{extension}", extensions=extension)

    db_manager = app.state.model_registry.DB.manager
    in_database = set(sa_inspect(db_manager._setup_engine).get_table_names())
    owned = {
        name
        for name, table in db_manager.Base.metadata.tables.items()
        if table.info.get("extension") == extension
    }
    assert owned <= in_database, f"missing tables: {sorted(owned - in_database)}"
