# SPDX-License-Identifier: AGPL-3.0-or-later
"""A provider instance's settings, usage and enabled abilities are the
instance's: they inherit its access, and writing one takes what changing
the instance takes.

Providers read an instance's settings as ROOT, on the server's behalf. Each
was once checked as ROOT on create (or not at all), so any user could plant
``api_base``/``verify_url`` on another user's (or the operator's) instance,
and its next call would carry that instance's credential to them, or a
forward-auth verifier would vouch for whoever they liked.
"""

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserTeamModel
from zephyrex.logic.BLL_Extensions import AbilityManager, ExtensionManager
from zephyrex.logic.BLL_Providers import (
    ProviderExtensionAbilityManager,
    ProviderExtensionManager,
    ProviderInstanceExtensionAbilityManager,
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderInstanceSettingModel,
    ProviderInstanceUsageManager,
    ProviderManager,
)
from zephyrex.pydantic2.registry import ModelRegistry
from zephyrex.testing.factories import add_user_to_team, create_team, create_user

ATTACKER_URL = "https://attacker.example.test"
CLEANUP_NAME = "provider_instance_settings_planted"
CLEANUP = (
    Path(__file__).parents[1]
    / "database"
    / "migrations"
    / "versions"
    / f"{CLEANUP_NAME}.py"
)


def _cleanup() -> ModuleType:
    """The migration that removed the settings planted before 6acbda48."""
    spec = importlib.util.spec_from_file_location(CLEANUP_NAME, CLEANUP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refused(status: int, call: Any, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(HTTPException) as refused:
        call(*args, **kwargs)
    assert refused.value.status_code == status, refused.value.detail


class TestProviderInstanceChildAccess:
    @pytest.fixture
    def provider_id(self, model_registry) -> str:
        with ProviderManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ) as providers:
            return str(providers.create(name=f"Access Provider {uuid.uuid4()}").id)

    @pytest.fixture
    def instance_of(self, model_registry, provider_id):
        def _make(owner_id: str, **fields: Any) -> Any:
            with ProviderInstanceManager(
                requester_id=owner_id, model_registry=model_registry
            ) as instances:
                return instances.create(
                    name=f"Instance {uuid.uuid4()}", provider_id=provider_id, **fields
                )

        return _make

    @staticmethod
    def settings(model_registry, requester_id: str) -> ProviderInstanceSettingManager:
        return ProviderInstanceSettingManager(
            requester_id=requester_id, model_registry=model_registry
        )

    @staticmethod
    def stored(model_registry, instance_id: str) -> Dict[str, Optional[str]]:
        """Every live setting on the instance, read as the server reads them
        (ROOT's reads include deleted rows, so those are filtered out)."""
        setting_db = ProviderInstanceSettingModel.DB(model_registry.DB.manager.Base)
        rows: List[Any] = setting_db.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            return_type="dto",
            override_dto=ProviderInstanceSettingModel,
            filters=[setting_db.deleted_at.is_(None)],
            provider_instance_id=instance_id,
        )
        return {row.key: row.value for row in rows}

    # -- settings: create -------------------------------------------------

    def test_no_setting_on_another_users_instance(
        self, admin_a, admin_b, server, model_registry, instance_of
    ):
        victim = instance_of(admin_a.id)
        with self.settings(model_registry, admin_b.id) as settings:
            _refused(
                404,
                settings.create,
                provider_instance_id=victim.id,
                key="api_base",
                value=ATTACKER_URL,
            )
            _refused(
                404,
                settings.create,
                entities=[
                    {
                        "provider_instance_id": victim.id,
                        "key": "base_url",
                        "value": ATTACKER_URL,
                    }
                ],
            )
        assert self.stored(model_registry, victim.id) == {}

    def test_no_setting_on_a_root_instance(
        self, admin_b, server, model_registry, instance_of
    ):
        operator = instance_of(env("ROOT_ID"), scope="root")
        with self.settings(model_registry, admin_b.id) as settings:
            _refused(
                404,
                settings.create,
                provider_instance_id=operator.id,
                key="verify_url",
                value=ATTACKER_URL,
            )
        assert self.stored(model_registry, operator.id) == {}

    def test_no_setting_on_a_system_instance(
        self, admin_b, server, model_registry, instance_of
    ):
        """A SYSTEM-created instance is visible to every user, but not
        theirs to configure."""
        operator = instance_of(env("SYSTEM_ID"), scope="system")
        with self.settings(model_registry, admin_b.id) as settings:
            _refused(
                403,
                settings.create,
                provider_instance_id=operator.id,
                key="url",
                value=ATTACKER_URL,
            )
        assert self.stored(model_registry, operator.id) == {}

    def test_an_operator_instance_is_the_operators_even_when_a_user_owns_it(
        self, admin_a, server, model_registry, instance_of
    ):
        """A root- or system-scoped instance speaks for the operator (an
        extension may trust what it vouches for), so only ROOT and SYSTEM
        configure it, whoever it names as its user."""
        operator = instance_of(env("ROOT_ID"), scope="system", user_id=admin_a.id)
        with self.settings(model_registry, admin_a.id) as settings:
            _refused(
                403,
                settings.create,
                provider_instance_id=operator.id,
                key="api_base",
                value=ATTACKER_URL,
            )
        assert self.stored(model_registry, operator.id) == {}

    # -- settings: read, update, delete ----------------------------------

    def test_another_users_settings_are_not_found(
        self, admin_a, admin_b, server, model_registry, instance_of
    ):
        victim = instance_of(admin_a.id)
        with self.settings(model_registry, admin_a.id) as settings:
            row = settings.create(
                provider_instance_id=victim.id, key="api_base", value="https://ok"
            )
        with self.settings(model_registry, admin_b.id) as settings:
            _refused(404, settings.get, id=row.id)
            assert settings.list(provider_instance_id=victim.id) == []
            _refused(404, settings.update, row.id, value=ATTACKER_URL)
            _refused(404, settings.delete, row.id)
        assert self.stored(model_registry, victim.id) == {"api_base": "https://ok"}

    def test_a_viewer_reads_but_does_not_configure(
        self, admin_b, user_b, team_b, server, model_registry, instance_of
    ):
        """A team member sees the team's instance and so its settings, but
        configuring it takes edit rights (a team admin's)."""
        shared = instance_of(admin_b.id, team_id=team_b.id)
        with self.settings(model_registry, admin_b.id) as settings:
            row = settings.create(
                provider_instance_id=shared.id, key="api_base", value="https://ok"
            )
        with self.settings(model_registry, user_b.id) as settings:
            assert settings.get(id=row.id).value == "https://ok"
            _refused(
                403,
                settings.create,
                provider_instance_id=shared.id,
                key="base_url",
                value=ATTACKER_URL,
            )
            _refused(403, settings.update, row.id, value=ATTACKER_URL)
            _refused(403, settings.delete, row.id)
        assert self.stored(model_registry, shared.id) == {"api_base": "https://ok"}

    def test_a_setting_stays_on_its_instance(
        self, admin_a, admin_b, server, model_registry, instance_of
    ):
        mine, other_of_mine = instance_of(admin_a.id), instance_of(admin_a.id)
        theirs = instance_of(admin_b.id)
        with self.settings(model_registry, admin_a.id) as settings:
            row = settings.create(
                provider_instance_id=mine.id, key="api_base", value="https://ok"
            )
            for target in (theirs.id, other_of_mine.id):
                _refused(400, settings.update, row.id, provider_instance_id=target)
            assert settings.get(id=row.id).provider_instance_id == mine.id
        assert self.stored(model_registry, theirs.id) == {}

    def test_the_owner_configures_their_instance(
        self, admin_a, server, model_registry, instance_of
    ):
        mine = instance_of(admin_a.id)
        with self.settings(model_registry, admin_a.id) as settings:
            row = settings.create(
                provider_instance_id=mine.id, key="api_base", value="https://one"
            )
            settings.update(row.id, value="https://two")
            assert self.stored(model_registry, mine.id) == {"api_base": "https://two"}
            settings.delete(row.id)
        assert self.stored(model_registry, mine.id) == {}

    def test_the_operator_configures_any_instance(
        self, admin_a, server, model_registry, instance_of
    ):
        for owner in (admin_a.id, env("ROOT_ID")):
            instance = instance_of(owner)
            for operator_id in (env("ROOT_ID"), env("SYSTEM_ID")):
                key = f"key_{uuid.uuid4().hex}"
                self.settings(model_registry, operator_id).create(
                    provider_instance_id=instance.id, key=key, value="v"
                )
                assert self.stored(model_registry, instance.id)[key] == "v"

    # -- settings planted before writes were checked ---------------------

    @staticmethod
    def plant(model_registry, instance_id: str, author_id: str, value: str) -> str:
        """A setting on ``instance_id`` recorded as ``author_id``'s, as rows
        written before 6acbda48 checked writes are: the manager refuses
        such a write now, so the row is written as ROOT and its author
        rewritten underneath."""
        row = ProviderInstanceSettingManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).create(provider_instance_id=instance_id, key="api_base", value=value)
        setting_db = ProviderInstanceSettingModel.DB(model_registry.DB.manager.Base)
        session = model_registry.DB.session()
        try:
            session.query(setting_db).filter(setting_db.id == row.id).update(
                {"created_by_user_id": author_id}
            )
            session.commit()
        finally:
            session.close()
        return str(row.id)

    @staticmethod
    def applied(model_registry, instance_id: str) -> Optional[str]:
        """What the instance's provider reads for ``api_base``. Providers
        read through the attached registry, which another test's app may
        hold, so this test's is attached for the read and the earlier one
        restored after."""
        instance = ProviderInstanceManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).get(id=instance_id)
        earlier = ModelRegistry.attached()
        model_registry.bind_app(model_registry.app)
        try:
            return ProviderInstanceModel.model_validate(
                instance, from_attributes=True
            ).get_setting("api_base")
        finally:
            if earlier is not None and earlier is not model_registry:
                earlier.bind_app(earlier.app)

    @staticmethod
    def planted(model_registry) -> Dict[str, List[str]]:
        """What the cleanup migration judges planted in the app's database
        (read only)."""
        engine = model_registry.DB.manager.get_setup_engine()
        with engine.connect() as conn:
            found: Dict[str, List[str]] = _cleanup().planted_setting_ids(conn)
        return found

    @classmethod
    def clean(cls, model_registry, instance_id: str) -> None:
        """Runs the cleanup over ``instance_id`` alone: this worker's
        database holds other tests' rows, which are not this test's to
        remove."""
        found = cls.planted(model_registry).get(instance_id, [])
        engine = model_registry.DB.manager.get_setup_engine()
        with engine.begin() as conn:
            _cleanup().tombstone(conn, {instance_id: found})

    # Changed with the decision "one-time cleanup instead": f225977e kept a
    # planted row from applying with a read-time author check in
    # get_setting, which also stopped a departed team admin's settings from
    # applying. The check is gone; the plant is removed by the migration,
    # so these tests show the cleanup finds and removes it.
    def test_a_planted_setting_is_not_its_planters_and_the_cleanup_removes_it(
        self, admin_a, admin_b, server, model_registry, instance_of
    ):
        victim = instance_of(admin_a.id)
        planted = self.plant(model_registry, victim.id, admin_b.id, ATTACKER_URL)
        with self.settings(model_registry, admin_b.id) as settings:
            _refused(404, settings.get, id=planted)
            assert planted not in {s.id for s in settings.list()}
            _refused(404, settings.update, planted, value=ATTACKER_URL)
        assert self.planted(model_registry)[victim.id] == [planted]
        self.clean(model_registry, victim.id)
        assert self.applied(model_registry, victim.id) is None

    def test_the_cleanup_removes_a_users_setting_on_an_operator_instance(
        self, admin_b, server, model_registry, instance_of
    ):
        """Only the operator configures a root- or system-scoped instance,
        so the cleanup removes anything a user wrote on one, though the
        instance is theirs to see (system) or theirs by name (root)."""
        for operator in (
            instance_of(env("SYSTEM_ID"), scope="system"),
            instance_of(env("ROOT_ID"), scope="root", user_id=admin_b.id),
        ):
            planted = self.plant(model_registry, operator.id, admin_b.id, ATTACKER_URL)
            with self.settings(model_registry, admin_b.id) as settings:
                _refused(403, settings.update, planted, value=ATTACKER_URL)
            assert self.planted(model_registry)[operator.id] == [planted]
            self.clean(model_registry, operator.id)
            assert self.applied(model_registry, operator.id) is None

    def test_settings_the_configurers_wrote_apply_and_are_kept(
        self, admin_a, server, model_registry, instance_of
    ):
        mine = instance_of(admin_a.id)
        with self.settings(model_registry, admin_a.id) as settings:
            settings.create(
                provider_instance_id=mine.id, key="api_base", value="https://own"
            )
        served = instance_of(admin_a.id)
        with self.settings(model_registry, env("SYSTEM_ID")) as settings:
            settings.create(
                provider_instance_id=served.id, key="api_base", value="https://op"
            )
        operator = instance_of(env("ROOT_ID"), scope="root")
        with self.settings(model_registry, env("ROOT_ID")) as settings:
            settings.create(
                provider_instance_id=operator.id, key="api_base", value="https://root"
            )
        planted = self.plant(model_registry, mine.id, str(uuid.uuid4()), ATTACKER_URL)
        assert self.planted(model_registry)[mine.id] == [planted]
        for instance in (mine, served, operator):
            self.clean(model_registry, instance.id)
        assert self.applied(model_registry, mine.id) == "https://own"
        assert self.applied(model_registry, served.id) == "https://op"
        assert self.applied(model_registry, operator.id) == "https://root"

    @pytest.fixture
    def team_with_two_admins(self, server, model_registry):
        """A fresh team (``founder`` made it, so administers it) with a
        second admin, ``admin``, and a plain ``member``."""
        founder, admin, member = (create_user(server) for _ in range(3))
        team = create_team(server, founder.id, name=f"Team {uuid.uuid4()}")
        add_user_to_team(server, admin.id, team.id, env("ADMIN_ROLE_ID"))
        add_user_to_team(server, member.id, team.id, env("USER_ROLE_ID"))
        return founder, admin, member, team

    @staticmethod
    def leave(model_registry, user_id: str, team_id: str) -> None:
        user_team_db = UserTeamModel.DB(model_registry.DB.manager.Base)
        for membership in user_team_db.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            return_type="dto",
            override_dto=UserTeamModel,
            filters=[user_team_db.deleted_at.is_(None)],
            user_id=user_id,
            team_id=team_id,
        ):
            user_team_db.delete(
                requester_id=env("ROOT_ID"),
                model_registry=model_registry,
                id=membership.id,
            )

    def test_a_team_admins_setting_still_applies_after_they_leave(
        self, server, model_registry, instance_of, team_with_two_admins
    ):
        """The reason for the decision. A team admin configures the team's
        instance, then leaves the team: the instance keeps running on what
        they set. f225977e's read-time check dropped the setting the moment
        they left (this fails on it)."""
        founder, admin, _, team = team_with_two_admins
        shared = instance_of(founder.id, team_id=team.id)
        with self.settings(model_registry, admin.id) as settings:
            settings.create(
                provider_instance_id=shared.id, key="api_base", value="https://team"
            )
        assert shared.id not in self.planted(model_registry)
        self.clean(model_registry, shared.id)

        self.leave(model_registry, admin.id, team.id)

        assert self.applied(model_registry, shared.id) == "https://team"
        with self.settings(model_registry, admin.id) as settings:
            _refused(
                404,
                settings.create,
                provider_instance_id=shared.id,
                key="base_url",
                value=ATTACKER_URL,
            )

    def test_the_cleanup_removes_a_team_members_plant_but_not_an_admins(
        self, server, model_registry, instance_of, team_with_two_admins
    ):
        founder, admin, member, team = team_with_two_admins
        shared = instance_of(founder.id, team_id=team.id)
        with self.settings(model_registry, admin.id) as settings:
            kept = settings.create(
                provider_instance_id=shared.id, key="base_url", value="https://admin"
            )
        planted = self.plant(model_registry, shared.id, member.id, ATTACKER_URL)
        found = self.planted(model_registry)[shared.id]
        assert found == [planted] and kept.id not in found
        self.clean(model_registry, shared.id)
        assert self.applied(model_registry, shared.id) is None
        assert self.stored(model_registry, shared.id) == {"base_url": "https://admin"}

    def test_a_deleted_setting_no_longer_applies(
        self, admin_a, server, model_registry, instance_of
    ):
        """get_setting read as ROOT, whose reads include deleted rows, so a
        setting its owner deleted went on steering the instance (and the
        cleanup's soft-delete would have changed nothing). This fails on
        the old get_setting."""
        mine = instance_of(admin_a.id)
        with self.settings(model_registry, admin_a.id) as settings:
            row = settings.create(
                provider_instance_id=mine.id, key="api_base", value="https://gone"
            )
            settings.delete(row.id)
        assert self.applied(model_registry, mine.id) is None

    # -- usage ------------------------------------------------------------

    def test_no_usage_on_another_users_instance(
        self, admin_a, admin_b, server, model_registry, instance_of
    ):
        victim = instance_of(admin_a.id)
        operator = instance_of(env("ROOT_ID"), scope="root")
        with ProviderInstanceUsageManager(
            requester_id=admin_b.id, model_registry=model_registry
        ) as usages:
            for instance in (victim, operator):
                _refused(
                    404,
                    usages.create,
                    provider_instance_id=instance.id,
                    key="input_tokens",
                    value=10**9,
                )

    def test_usage_is_recorded_only_as_yourself(
        self, admin_a, admin_b, server, model_registry, instance_of
    ):
        mine = instance_of(admin_a.id)
        with ProviderInstanceUsageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as usages:
            _refused(
                403,
                usages.create,
                provider_instance_id=mine.id,
                key="input_tokens",
                value=10,
                user_id=admin_b.id,
            )
        recorded = ProviderInstanceUsageManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).create(
            provider_instance_id=mine.id,
            key="input_tokens",
            value=10,
            user_id=admin_b.id,
        )
        assert recorded.user_id == admin_b.id

    # -- enabled abilities -------------------------------------------------

    @pytest.fixture
    def provider_extension_ability_id(self, model_registry, provider_id) -> str:
        """An ability the provider offers, seeded as the framework seeds
        them (as SYSTEM)."""
        system = env("SYSTEM_ID")
        extension = ExtensionManager(
            requester_id=system, model_registry=model_registry
        ).create(
            name=f"access_ext_{uuid.uuid4().hex}",
            description="An extension for access tests",
            friendly_name="Access Extension",
        )
        ability = AbilityManager(
            requester_id=system, model_registry=model_registry
        ).create(
            name=f"access_ability_{uuid.uuid4().hex}",
            extension_id=extension.id,
            meta=False,
            friendly_name="Access Ability",
        )
        provider_extension = ProviderExtensionManager(
            requester_id=system, model_registry=model_registry
        ).create(provider_id=provider_id, extension_id=extension.id)
        return str(
            ProviderExtensionAbilityManager(
                requester_id=system, model_registry=model_registry
            )
            .create(provider_extension_id=provider_extension.id, ability_id=ability.id)
            .id
        )

    def test_no_ability_enabled_on_another_users_instance(
        self,
        admin_a,
        admin_b,
        server,
        model_registry,
        instance_of,
        provider_extension_ability_id,
    ):
        victim = instance_of(admin_a.id)
        operator = instance_of(env("ROOT_ID"), scope="root")
        with ProviderInstanceExtensionAbilityManager(
            requester_id=admin_b.id, model_registry=model_registry
        ) as abilities:
            for instance in (victim, operator):
                _refused(
                    404,
                    abilities.create,
                    provider_instance_id=instance.id,
                    provider_extension_ability_id=provider_extension_ability_id,
                    forced=True,
                )
        assert (
            ProviderInstanceExtensionAbilityManager(
                requester_id=env("ROOT_ID"), model_registry=model_registry
            ).list(provider_instance_id=victim.id)
            == []
        )

    def test_the_owner_enables_abilities_on_their_instance(
        self,
        admin_a,
        admin_b,
        server,
        model_registry,
        instance_of,
        provider_extension_ability_id,
    ):
        mine = instance_of(admin_a.id)
        with ProviderInstanceExtensionAbilityManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as abilities:
            enabled = abilities.create(
                provider_instance_id=mine.id,
                provider_extension_ability_id=provider_extension_ability_id,
            )
            _refused(
                404,
                abilities.create,
                provider_instance_id=mine.id,
                provider_extension_ability_id=str(uuid.uuid4()),
            )
        with ProviderInstanceExtensionAbilityManager(
            requester_id=admin_b.id, model_registry=model_registry
        ) as abilities:
            _refused(404, abilities.update, enabled.id, forced=True)
            _refused(404, abilities.delete, enabled.id)
