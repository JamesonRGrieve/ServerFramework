"""Tests for the auth_privacy data-residency extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "auth_privacy_test")

import pytest
from fastapi import HTTPException

from zephyrex.extensions.auth_privacy.BLL_Auth_Privacy import (
    DataRegionManager,
    DataRegionModel,
    DataResidencyPreferenceManager,
    DataResidencyPreferenceModel,
)
from zephyrex.extensions.auth_privacy.EXT_Auth_Privacy import (
    EXT_Auth_Privacy,
)


class TestCanonicalWiring:
    def test_data_region_manager_round_trip(self):
        assert DataRegionModel.Manager is DataRegionManager
        assert DataRegionManager._model is DataRegionModel

    def test_data_residency_manager_round_trip(self):
        assert DataResidencyPreferenceModel.Manager is DataResidencyPreferenceManager
        assert (
            DataResidencyPreferenceManager._model is DataResidencyPreferenceModel
        )

    def test_extension_metadata(self):
        assert EXT_Auth_Privacy.name == "auth_privacy"
        abilities = EXT_Auth_Privacy.get_abilities()
        assert "data_residency_register_region" in abilities
        assert "data_residency_pin_tenant" in abilities


class TestUserXorTeamValidation:
    def test_neither_set_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            DataResidencyPreferenceModel.Create(
                data_type="user_data", region_id="r1"
            )
        assert exc_info.value.status_code == 422

    def test_both_set_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            DataResidencyPreferenceModel.Create(
                data_type="user_data",
                region_id="r1",
                user_id="u1",
                team_id="t1",
            )
        assert exc_info.value.status_code == 422

    def test_user_only_accepted(self):
        record = DataResidencyPreferenceModel.Create(
            data_type="user_data", region_id="r1", user_id="u1"
        )
        assert record.user_id == "u1"
        assert record.team_id is None

    def test_team_only_accepted(self):
        record = DataResidencyPreferenceModel.Create(
            data_type="files", region_id="r1", team_id="t1"
        )
        assert record.team_id == "t1"
        assert record.user_id is None


class TestLifecycle:
    def test_on_initialize_returns_true(self):
        assert EXT_Auth_Privacy.on_initialize() is True
