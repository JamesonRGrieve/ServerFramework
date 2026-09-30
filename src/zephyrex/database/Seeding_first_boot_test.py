# SPDX-License-Identifier: AGPL-3.0-or-later
"""A brand-new database is fully seeded by its first boot. Rows that name
their parent (an ability its extension, a provider instance its provider)
used to be skipped when the parent was seeded later in the same boot, and
appeared only after a restart."""

import uuid

from sqlalchemy import func, select

from zephyrex.lib.Environment import refresh_settings
from zephyrex.logic.BLL_Extensions import AbilityModel, ExtensionModel
from zephyrex.logic.BLL_Providers import (
    ProviderExtensionModel,
    ProviderInstanceModel,
    ProviderModel,
)


def _count(registry, model) -> int:
    db_cls = model.DB(registry.DB.manager.Base)
    session = registry.DB.session()
    return int(session.execute(select(func.count()).select_from(db_cls)).scalar())


def test_the_first_boot_seeds_every_row(tmp_path, monkeypatch):
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path))
    refresh_settings()
    try:
        prepare_test_registry()
        app = instance(
            db_prefix=f"test.first_boot.{uuid.uuid4().hex[:8]}",
            extensions="email",
        )
        registry = app.state.model_registry

        providers = _count(registry, ProviderModel)
        assert _count(registry, ExtensionModel) >= 1
        assert providers >= 1
        # Children of those rows, seeded in the same boot:
        assert _count(registry, AbilityModel) >= 1
        assert _count(registry, ProviderExtensionModel) == providers
        assert _count(registry, ProviderInstanceModel) == providers
    finally:
        monkeypatch.delenv("DATABASE_PATH")
        refresh_settings()
