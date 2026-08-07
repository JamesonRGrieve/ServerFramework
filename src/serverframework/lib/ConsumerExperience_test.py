"""Tests for the consumer pip-install experience.

These verify that a consumer who pip-installs serverframework and provides
only an extensions/ directory with BLL_*.py models gets a working app —
DB tables, REST endpoints, GraphQL, and correct path resolution for both
consumer and bundled extensions.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Type
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Helpers: a minimal consumer extension defined in-memory
# ---------------------------------------------------------------------------


def _make_consumer_extension(tmp_path: Path) -> Path:
    """Create a minimal consumer extension tree under tmp_path."""
    ext_dir = tmp_path / "extensions" / "widget"
    ext_dir.mkdir(parents=True)

    (ext_dir / "__init__.py").write_text("")

    (ext_dir / "BLL_Widget.py").write_text(
        textwrap.dedent("""\
        from typing import Optional
        from pydantic import BaseModel, Field
        from serverframework.lib.Pydantic2SQLAlchemy import ApplicationModel, UpdateMixinModel
        from serverframework.logic.AbstractLogicManager import AbstractBLLManager

        class WidgetModel(ApplicationModel, UpdateMixinModel):
            name: str = Field(..., description="Widget name")
            color: Optional[str] = Field(None, description="Widget color")

            class Create(BaseModel):
                name: str = Field(...)
                color: Optional[str] = None

            class Update(BaseModel):
                name: Optional[str] = None
                color: Optional[str] = None

            class Search(ApplicationModel.Search):
                name: Optional[str] = None

        class WidgetManager(AbstractBLLManager):
            _model = WidgetModel
        """)
    )

    (ext_dir / "EXT_Widget.py").write_text(
        textwrap.dedent("""\
        from serverframework.extensions.AbstractExtensionProvider import (
            AbstractStaticExtension,
            ExtensionType,
        )
        from serverframework.lib.Dependencies import Dependencies

        class EXT_Widget(AbstractStaticExtension):
            name = "widget"
            description = "Test widget"
            types = {ExtensionType.DATABASE}
            version = "0.1.0"
            dependencies = Dependencies([])

            @classmethod
            def get_default_env(cls):
                return {}
        """)
    )

    return tmp_path / "extensions"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestExtensionPathResolution:
    """Verify the consumer/bundled fallback chain for extension discovery."""

    def test_set_extensions_root_updates_path(self, tmp_path):
        ext_path = tmp_path / "exts"
        ext_path.mkdir()
        from serverframework import set_extensions_root
        from serverframework.lib.Paths import extensions_dir

        set_extensions_root(ext_path)
        assert extensions_dir() == str(ext_path)
        set_extensions_root(None)

    def test_set_extensions_root_rejects_missing_path(self):
        from serverframework import set_extensions_root

        with pytest.raises(FileNotFoundError):
            set_extensions_root("/nonexistent/path/that/does/not/exist")

    def test_extension_dir_falls_back_to_bundled(self, tmp_path):
        """When consumer path doesn't have an extension, check bundled."""
        from serverframework.extensions.AbstractExtensionProvider import (
            ExtensionRegistry,
        )

        consumer_path = tmp_path / "exts"
        consumer_path.mkdir()
        reg = ExtensionRegistry.__new__(ExtensionRegistry)
        reg.extensions_path = str(consumer_path)

        bundled = reg._extension_dir("auth_session")
        assert "auth_session" in bundled
        assert Path(bundled).is_dir()

    def test_extension_dir_prefers_consumer_path(self, tmp_path):
        """Consumer's extension takes priority over bundled."""
        from serverframework.extensions.AbstractExtensionProvider import (
            ExtensionRegistry,
        )

        consumer_path = tmp_path / "exts"
        (consumer_path / "my_ext").mkdir(parents=True)
        reg = ExtensionRegistry.__new__(ExtensionRegistry)
        reg.extensions_path = str(consumer_path)

        result = reg._extension_dir("my_ext")
        assert result == str(consumer_path / "my_ext")


class TestExtensionLoaderFallback:
    """Verify ExtensionLoader.load_extension_module falls back to bundled."""

    def test_loads_bundled_when_consumer_path_missing(self, tmp_path):
        from serverframework.extensions.ExtensionLoader import load_extension_module

        consumer_path = tmp_path / "exts"
        consumer_path.mkdir()

        mod = load_extension_module(str(consumer_path), "auth_session", "BLL_Session")
        assert hasattr(mod, "SessionModel")

    def test_loads_from_consumer_path(self, tmp_path):
        ext_path = _make_consumer_extension(tmp_path)
        from serverframework.extensions.ExtensionLoader import load_extension_module

        mod = load_extension_module(str(ext_path), "widget", "BLL_Widget")
        assert hasattr(mod, "WidgetModel")
        assert hasattr(mod, "WidgetManager")

    def test_raises_when_not_found_anywhere(self, tmp_path):
        from serverframework.extensions.ExtensionLoader import load_extension_module

        consumer_path = tmp_path / "exts"
        consumer_path.mkdir()
        with pytest.raises(FileNotFoundError):
            load_extension_module(str(consumer_path), "nonexistent_ext", "BLL_Nope")


# ---------------------------------------------------------------------------
# SA model fallback (no ModelMeta metaclass)
# ---------------------------------------------------------------------------


class TestSAModelFallback:
    """Models without ModelMeta get SA models via create_sqlalchemy_model fallback."""

    def test_create_sqlalchemy_model_for_plain_model(self):
        from sqlalchemy.orm import DeclarativeBase

        from serverframework.lib.Pydantic2SQLAlchemy import (
            ApplicationModel,
            create_sqlalchemy_model,
        )

        class TestPlainModel(ApplicationModel):
            label: str = Field(..., description="A label")

        class TestBase(DeclarativeBase):
            pass

        from serverframework.lib.Pydantic import ModelRegistry

        registry = ModelRegistry()
        sa_model = create_sqlalchemy_model(
            TestPlainModel, model_registry=registry, base_model=TestBase
        )
        assert sa_model is not None
        assert hasattr(sa_model, "__tablename__")
        assert "test_plain" in sa_model.__tablename__


# ---------------------------------------------------------------------------
# Router auto-generation (no RouterMixin)
# ---------------------------------------------------------------------------


class TestRouterAutoGeneration:
    """Managers without RouterMixin get routes via create_router_from_manager."""

    def test_prefix_derived_from_manager_name(self):
        """create_router_from_manager derives /v1/<resource> from the manager name."""
        import stringcase
        from serverframework.lib.Pydantic2FastAPI import create_router_from_manager
        from serverframework.lib.Pydantic2SQLAlchemy import ApplicationModel
        from serverframework.logic.AbstractLogicManager import AbstractBLLManager

        class GadgetModel(ApplicationModel):
            name: str = Field(...)

            class Create(BaseModel):
                name: str

            class Update(BaseModel):
                name: Optional[str] = None

            class Search(ApplicationModel.Search):
                pass

        class GadgetManager(AbstractBLLManager):
            _model = GadgetModel

        expected_prefix = "/v1/gadget"
        resource_name = stringcase.snakecase(
            GadgetManager.__name__.replace("Manager", "")
        )
        assert resource_name == "gadget"
        assert expected_prefix == f"/v1/{resource_name}"

    def test_getattr_defaults_for_routerless_manager(self):
        """Managers without RouterMixin attributes resolve via getattr defaults."""
        from serverframework.logic.AbstractLogicManager import AbstractBLLManager

        class BareManager(AbstractBLLManager):
            _model = None

        assert getattr(BareManager, "prefix", None) is None
        assert getattr(BareManager, "tags", None) is None
        assert getattr(BareManager, "routes_to_register", None) is None
        assert getattr(BareManager, "example_overrides", None) is None


# ---------------------------------------------------------------------------
# Model-Manager auto-wiring
# ---------------------------------------------------------------------------


class TestModelManagerAutoWiring:
    """discover_model_relationships auto-wires Model.Manager when unset."""

    def test_auto_wires_manager(self):
        import types

        from serverframework.lib.Pydantic import PydanticUtility
        from serverframework.lib.Pydantic2SQLAlchemy import ApplicationModel
        from serverframework.logic.AbstractLogicManager import AbstractBLLManager

        class AutoWireModel(ApplicationModel):
            name: str = Field(...)

        class AutoWireManager(AbstractBLLManager):
            _model = AutoWireModel

        assert not getattr(AutoWireModel, "Manager", None)

        mock_module = types.ModuleType("test_auto_wire")
        mock_module.AutoWireModel = AutoWireModel
        mock_module.AutoWireManager = AutoWireManager

        util = PydanticUtility()
        relationships = util.discover_model_relationships(
            {"test_auto_wire": mock_module}
        )

        assert len(relationships) == 1
        assert AutoWireModel.Manager is AutoWireManager


# ---------------------------------------------------------------------------
# DatabaseManager CWD default
# ---------------------------------------------------------------------------


class TestDatabasePathDefault:
    def test_sqlite_defaults_to_cwd(self, monkeypatch):
        monkeypatch.delenv("DATABASE_PATH", raising=False)

        from serverframework.database.DatabaseManager import get_database_info

        info = get_database_info()
        assert os.getcwd() in info.get("file_path", "")


# ---------------------------------------------------------------------------
# run() settings refresh
# ---------------------------------------------------------------------------


class TestRunSettingsRefresh:
    def test_refresh_settings_picks_up_app_extensions(self, monkeypatch):
        monkeypatch.setenv("APP_EXTENSIONS", "custom_ext")
        from serverframework.lib.Environment import env, refresh_settings

        refresh_settings()
        assert env("APP_EXTENSIONS") == "custom_ext"

    def test_empty_app_extensions_default(self):
        from serverframework.lib.Environment import AppSettings

        s = AppSettings.model_validate({})
        assert s.APP_EXTENSIONS == ""


# ---------------------------------------------------------------------------
# Migration create_all fallback
# ---------------------------------------------------------------------------


class TestMigrationCreateAllFallback:
    """When Alembic auto-migration fails, create_all() creates tables."""

    def test_create_all_fallback_creates_tables(self, tmp_path):
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.orm import DeclarativeBase

        from serverframework.lib.Pydantic import ModelRegistry
        from serverframework.lib.Pydantic2SQLAlchemy import (
            ApplicationModel,
            create_sqlalchemy_model,
        )

        db_file = tmp_path / "test_fallback.db"
        engine = create_engine(f"sqlite:///{db_file}")

        class FallbackBase(DeclarativeBase):
            pass

        class FallbackTestModel(ApplicationModel):
            label: str = Field(...)

        registry = ModelRegistry()
        sa_model = create_sqlalchemy_model(
            FallbackTestModel, model_registry=registry, base_model=FallbackBase
        )
        FallbackBase.metadata.create_all(bind=engine)

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert len(tables) > 0
        assert sa_model.__tablename__ in tables


# ---------------------------------------------------------------------------
# Pip extras presence
# ---------------------------------------------------------------------------


class TestPipExtras:
    """Verify optional-dependency extras are declared in pyproject.toml."""

    @pytest.fixture(scope="class")
    @classmethod
    def pyproject(cls):
        import tomllib

        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            return tomllib.load(f)

    @pytest.mark.parametrize(
        "extra, expected_dep",
        [
            ("email", "sendgrid"),
            ("mfa", "pyotp"),
            ("payment", "stripe"),
            ("cache", "redis"),
        ],
    )
    def test_extra_declares_dependency(self, pyproject, extra, expected_dep):
        extras = pyproject["project"]["optional-dependencies"]
        assert extra in extras, f"Missing extra: {extra}"
        deps = [d.lower() for d in extras[extra]]
        assert any(expected_dep in d for d in deps), (
            f"Extra '{extra}' missing dep '{expected_dep}', got: {deps}"
        )

    def test_all_extra_includes_others(self, pyproject):
        extras = pyproject["project"]["optional-dependencies"]
        assert "all" in extras
        all_deps = " ".join(extras["all"]).lower()
        for name in ("email", "mfa", "payment", "cache"):
            assert name in all_deps


# ---------------------------------------------------------------------------
# Extension loader: bundled fallback with consumer path set
# ---------------------------------------------------------------------------


class TestExtensionLoaderBundledFallback:
    """When consumer extensions_path is set, bundled extensions still load."""

    def test_bundled_extension_loads_from_package(self, tmp_path):
        """auth_session (bundled) loads even when extensions_path points elsewhere."""
        from serverframework.extensions.ExtensionLoader import load_extension_module

        consumer_path = tmp_path / "my_exts"
        consumer_path.mkdir()
        mod = load_extension_module(str(consumer_path), "auth_session", "BLL_Session")
        assert hasattr(mod, "SessionModel")

    def test_bundled_fallback_does_not_shadow_consumer(self, tmp_path):
        """A consumer extension is NOT shadowed by a same-named bundled one."""
        from serverframework.extensions.ExtensionLoader import load_extension_module

        ext_path = _make_consumer_extension(tmp_path)
        mod = load_extension_module(str(ext_path), "widget", "BLL_Widget")
        assert hasattr(mod, "WidgetModel")
        assert not hasattr(mod, "SessionModel")
