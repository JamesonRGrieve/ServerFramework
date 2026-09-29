# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral tests for zephyrex.mypy_plugin: run mypy on a probe module with
the plugin enabled and assert on its diagnostics and revealed types."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Dict, List

import pytest
from mypy import api as mypy_api

from zephyrex.logic.AbstractLogicManager.models import ModelMeta
from zephyrex.mypy_plugin import _reference_field_name

SRC_ROOT = Path(__file__).resolve().parent.parent

PROBE = textwrap.dedent("""
    from typing import Optional as Opt

    from zephyrex.logic.AbstractLogicManager.models import (
        ApplicationModel,
        ModelMeta,
        UpdateMixinModel,
    )
    from zephyrex.pydantic2.sqlalchemy import ApplicationModel as PlainApplication
    from zephyrex.pydantic2.sqlalchemy import UpdateMixinModel as PlainUpdateMixin


    class ItemInstanceModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
        name: str


    class OwnerModel(
        ApplicationModel,
        UpdateMixinModel,
        ItemInstanceModel.Reference.Optional,
        metaclass=ModelMeta,
    ):
        class Search(ApplicationModel.Search, UpdateMixinModel.Search):
            pass


    class PlainRecord(PlainApplication, PlainUpdateMixin):
        pass


    reveal_type(ItemInstanceModel.Reference.ID().item_instance_id)
    reveal_type(ItemInstanceModel.Reference.ID.Optional().item_instance_id)
    reveal_type(ItemInstanceModel.Reference.ID.Search().item_instance_id)
    reveal_type(ItemInstanceModel.Reference().item_instance)
    reveal_type(ItemInstanceModel.Reference.Optional().item_instance)
    reveal_type(OwnerModel.Optional)
    reveal_type(OwnerModel.Search)
    reveal_type(PlainRecord)
    reveal_type(ItemInstanceModel.Reference().item_instance_id)
    """)

MYPY_CONFIG = textwrap.dedent("""
    [mypy]
    python_version = 3.11
    ignore_missing_imports = True
    follow_imports = silent
    plugins = zephyrex.mypy_plugin, pydantic.mypy
    """)

REVEAL = re.compile(r":(\d+): note: Revealed type is \"(.+)\"")
ERROR = re.compile(r":(\d+): error: (.+)")


@pytest.fixture(scope="module")
def mypy_run(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, List[str]]:
    work = tmp_path_factory.mktemp("mypy_plugin")
    probe = work / "probe.py"
    probe.write_text(PROBE)
    config = work / "mypy.ini"
    # The editable install resolves zephyrex through an import hook mypy
    # cannot follow; point mypy at the source tree directly.
    config.write_text(MYPY_CONFIG + f"mypy_path = {SRC_ROOT}\n")
    stdout, stderr, _ = mypy_api.run(
        [
            "--config-file",
            str(config),
            "--cache-dir",
            str(work / ".mypy_cache"),
            str(probe),
        ]
    )
    assert "Traceback" not in stdout + stderr, stdout + stderr
    lines = stdout.splitlines()
    return {
        "reveals": [m.group(2) for line in lines if (m := REVEAL.search(line))],
        "errors": [m.group(2) for line in lines if (m := ERROR.search(line))],
    }


class TestReferenceFamily:
    def test_field_naming_matches_runtime_model_meta(self):
        class Dummy:
            pass

        for model_name in ("ItemInstanceModel", "UserModel", "Team"):
            runtime = ModelMeta._create_reference_class(Dummy, model_name)
            runtime_field = next(iter(runtime.ID.__annotations__))
            assert f"{_reference_field_name(model_name)}_id" == runtime_field

    def test_id_classes_carry_the_foreign_key(self, mypy_run):
        reveals = mypy_run["reveals"]
        assert reveals[0] == "str"
        assert reveals[1] == "str | None"
        assert (
            reveals[2]
            == "zephyrex.logic.AbstractLogicManager.models.StringSearchModel | None"
        )

    def test_reference_classes_carry_the_related_model(self, mypy_run):
        reveals = mypy_run["reveals"]
        assert reveals[3] == "probe.ItemInstanceModel | None"
        assert reveals[4] == "probe.ItemInstanceModel | None"

    def test_reference_inherits_the_foreign_key_from_id(self, mypy_run):
        """``Reference`` subclasses ``Reference.ID`` at runtime; a model that
        mixes in ``X.Reference`` must see ``x_id`` as well as ``x``."""
        assert mypy_run["reveals"][8] == "str"

    def test_reference_is_usable_as_a_base(self, mypy_run):
        assert not any("not defined" in e for e in mypy_run["errors"])


class TestNestedNamespaces:
    def test_conflicting_mixin_namespace_binds_to_mro_first(self, mypy_run):
        assert "ApplicationModel.Optional" in mypy_run["reveals"][5]
        assert not any("in base class" in e for e in mypy_run["errors"])

    def test_class_defined_namespace_is_left_alone(self, mypy_run):
        assert mypy_run["reveals"][6].endswith("-> probe.OwnerModel.Search")


class TestPydanticComposition:
    def test_pydantic_still_transforms_basemodel_subclasses(self, mypy_run):
        """PlainRecord's bases are claimed by this plugin; pydantic's own
        transform must still run and synthesize the field-wise __init__
        (plain BaseModel.__init__ would be ``def (**data: Any)``)."""
        plain_record = mypy_run["reveals"][7]
        assert "id: " in plain_record and "updated_by_user_id: " in plain_record
        assert plain_record.endswith("-> probe.PlainRecord")

    def test_probe_type_checks_clean(self, mypy_run):
        assert mypy_run["errors"] == []
