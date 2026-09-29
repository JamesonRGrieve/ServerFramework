# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import re
import uuid
from datetime import datetime, timedelta

import pytest
from faker import Faker
from fastapi import HTTPException

from zephyrex.AbstractTest import ParentEntity
from zephyrex.database.AbstractDBTest import AbstractDBTest
from zephyrex.lib.Environment import env

# Import BLL models which will be converted to SQLAlchemy models via .DB()
from zephyrex.logic.BLL_Auth import (
    SessionModel,
    FailedLoginAttemptModel,
    InviteeModel,
    InvitationModel,
    MetadataModel,
    PermissionModel,
    RateLimitPolicyModel,
    RoleModel,
    TeamModel,
    UserCredentialModel,
    UserModel,
    UserRecoveryQuestionModel,
    UserTeamModel,
)
from zephyrex.testing.factories import (
    add_user_to_team,
    create_team,
    create_user,
    generate_test_email,
)

faker = Faker()


# Dynamically generate dependency name from filename
def _get_test_dependency_name():
    """Generate a dependency name from the current test file name."""
    filename = os.path.basename(__file__)
    # Remove .py extension and convert to snake_case
    name = re.sub(r"\.py$", "", filename).lower()
    # Ensure it ends with _tests (convert _test to _tests)
    name = re.sub(r"_test$", "_tests", name)
    if not name.endswith("_tests"):
        name += "_tests"
    return name


class TestUser(AbstractDBTest):
    class_under_test = UserModel
    create_fields = {
        "email": faker.unique.email,
        "display_name": lambda: faker.unique.user_name().upper(),
        "first_name": faker.first_name,
        "last_name": faker.last_name,
    }
    update_fields = {
        "display_name": "Updated UserModel",
        "first_name": "Updated",
        "last_name": "Name",
    }
    unique_fields = ["email"]

    def _get_user_as(self, requester_id: str, user_id: str):
        return UserModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=requester_id,
            model_registry=self.model_registry,
            return_type="dict",
            id=user_id,
        )

    def test_real_accounts_visible_without_shared_team(self, server, model_registry):
        """Every real account is VIEW-visible to an authenticated user, even
        with no shared team, so identifiers surfaced through related entities
        resolve (StaticPermissions' users-table rule)."""
        self._server = server
        self.model_registry = model_registry
        self.ensure_model(server)
        team_user = create_user(server, email=generate_test_email("perm_team_user"))
        create_team(server, team_user.id, name="Permission Test Team")
        isolated_user = create_user(server, email=generate_test_email("perm_isolated"))

        retrieved = self._get_user_as(team_user.id, isolated_user.id)
        assert retrieved is not None, "Real account is not visible for VIEW"
        assert retrieved["id"] == isolated_user.id, "Retrieved wrong user"

    @pytest.mark.parametrize("system_id_var", ["ROOT_ID", "SYSTEM_ID", "TEMPLATE_ID"])
    def test_system_accounts_are_not_visible(
        self, server, model_registry, system_id_var
    ):
        """The system accounts are excluded from the users-table VIEW rule; the
        generic grant on SYSTEM-created rows (which is how they are seeded)
        must not expose them either."""
        self._server = server
        self.model_registry = model_registry
        self.ensure_model(server)
        user = create_user(server, email=generate_test_email("no_system_accounts"))

        with pytest.raises(HTTPException) as denied:
            self._get_user_as(user.id, env(system_id_var))
        assert denied.value.status_code == 404

    def test_team_based_user_visibility(self, server, model_registry):
        """Members of the same team can see each other."""
        self._server = server
        self.model_registry = model_registry
        self.ensure_model(server)
        team_user = create_user(server, email=generate_test_email("visibility_a"))
        team = create_team(server, team_user.id, name="Visibility Test Team")
        other_user = create_user(server, email=generate_test_email("visibility_b"))
        add_user_to_team(server, other_user.id, team.id, env("USER_ROLE_ID"))

        retrieved = self._get_user_as(other_user.id, team_user.id)
        assert retrieved is not None, "Team member cannot see another team member"
        assert retrieved["id"] == team_user.id, "Retrieved wrong user"

    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_create(
        self, db, server, model_registry, admin_a, team_a, return_type
    ):
        self.db = db
        self._server = server  # Store server for access by helper methods
        self.model_registry = model_registry
        self.ensure_model(server)
        self._CRUD_create(return_type, admin_a.id, team_a.id)
        self._create_assert("CRUD_create_" + return_type)

    def test_ORM_create(self, db, server, model_registry, admin_a, team_a):
        self.db = db
        self._server = server  # Store server for access by helper methods
        self.model_registry = model_registry
        self.ensure_model(server)
        self._ORM_create()
        self._create_assert("ORM_create")

    # User records self-set ``created_by_user_id`` to the new user's id (see
    # AbstractDatabaseEntity.create), so the test creator (admin_a) has no
    # permission claim on the resulting record. Override the inherited CRUD
    # tests to operate as the new user themselves (or as ROOT for list).
    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_get(self, server, admin_a, team_a, return_type):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        self._CRUD_create("dict", admin_a.id, team_a.id, "CRUD_get")
        new_user_id = self.tracked_entities["CRUD_get"]["id"]
        self._CRUD_get(return_type, new_user_id, team_a.id)
        self._get_assert("CRUD_get_" + return_type)

    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_list(self, server, admin_a, team_a, return_type):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        # Use ROOT_ID for User listing: arbitrary cross-user visibility is not
        # granted to non-root callers, but the underlying ORM list is what's
        # under test here.
        self._CRUD_create("dict", env("ROOT_ID"), team_a.id, "CRUD_list_1")
        self._CRUD_create("dict", env("ROOT_ID"), team_a.id, "CRUD_list_2")
        self._CRUD_create("dict", env("ROOT_ID"), team_a.id, "CRUD_list_3")
        self._CRUD_list(return_type, env("ROOT_ID"), team_a.id)
        self._list_assert("CRUD_list_" + return_type)

    @pytest.mark.parametrize("return_type", sorted(["dict", "db", "model"]))
    def test_CRUD_update(self, server, admin_a, team_a, return_type):
        self.db = (
            server.app.state.model_registry.database_manager.get_session()
            if not self.db
            else self.db
        )
        self._server = server
        self.model_registry = server.app.state.model_registry
        self.ensure_model(server)
        if not hasattr(self.sqlalchemy_model, "updated_at"):
            pytest.skip("No ability to update.")
        self._CRUD_create("dict", admin_a.id, team_a.id, "CRUD_update")
        new_user_id = self.tracked_entities["CRUD_update"]["id"]
        updated_fields = self._CRUD_update(return_type, new_user_id, team_a.id)
        self._update_assert("CRUD_update_" + return_type, updated_fields)

    # @pytest.mark.dependency(depends=["test_CRUD_create"])
    def test_CRUD_delete(self, db, server, model_registry, admin_a, team_a):
        self.db = db
        self._server = server  # Store server for access by helper methods
        self.model_registry = model_registry
        self.ensure_model(server)
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_delete",
        )
        # For User records, the requester must be the same as the record being deleted
        # Since users can only delete themselves
        user_record = self.tracked_entities["CRUD_delete"]
        self._CRUD_delete(
            user_record["id"], team_a.id
        )  # Use the created user's ID as requester
        self._delete_assert(user_record["id"], "CRUD_delete")

    # @pytest.mark.dependency(depends=["test_CRUD_delete", "test_CRUD_get"])
    def test_CRUD_soft_delete(self, db, server, model_registry, admin_a, team_a):
        self.db = db
        self._server = server  # Store server for access by helper methods
        self.model_registry = model_registry
        self.ensure_model(server)
        self._CRUD_create(
            "dict",
            admin_a.id,
            team_a.id,
            "CRUD_delete",
        )
        # For User records, the requester must be the same as the record being deleted
        # Since users can only delete themselves
        user_record = self.tracked_entities["CRUD_delete"]
        self._CRUD_delete(
            user_record["id"], team_a.id
        )  # Use the created user's ID as requester
        # After Item 75 the soft-delete filter applies at the DB layer for
        # every read; verify the row still exists in storage by bypassing the
        # auto-filter via `include_deleted`, mirroring how admin/audit code is
        # expected to inspect tombstoned rows.
        from zephyrex.database.AbstractDatabaseEntity import include_deleted

        session = self.model_registry.DB.session()
        with include_deleted(session):
            row = (
                session.query(self.sqlalchemy_model)
                .filter(self.sqlalchemy_model.id == user_record["id"])
                .first()
            )
        assert row is not None, (
            f"{self.sqlalchemy_model.__name__}: soft-deleted row should remain "
            "in storage but is missing under include_deleted()"
        )
        assert row.deleted_at is not None, (
            f"{self.sqlalchemy_model.__name__}: deleted_at not set after " "soft-delete"
        )


class TestUserCredential(AbstractDBTest):
    class_under_test = UserCredentialModel
    parent_entities = [
        ParentEntity(name="UserModel", foreign_key="user_id", test_class=TestUser)
    ]
    create_fields = {
        "user_id": None,  # Will be populated by parent_entities
        "password_hash": "test_hash",
        "password_salt": "test_salt",
    }
    update_fields = {
        "password_hash": "updated_hash",
        "password_salt": "updated_salt",
    }


class TestUserRecoveryQuestion(AbstractDBTest):
    class_under_test = UserRecoveryQuestionModel
    parent_entities = [
        ParentEntity(name="UserModel", foreign_key="user_id", test_class=TestUser)
    ]
    create_fields = {
        "user_id": None,  # Will be populated by parent_entities
        "question": "Test security question?",
        "answer": "Test answer",
    }
    update_fields = {
        "question": "Updated security question?",
        "answer": "Updated answer",
    }


class TestTeam(AbstractDBTest):
    class_under_test = TeamModel
    create_fields = {
        "name": "Test TeamModel",
        "description": "Test TeamModel description",
        "encryption_salt": "test_key",
    }
    update_fields = {
        "name": "Updated TeamModel",
        "description": "Updated TeamModel description",
    }
    unique_fields = ["name"]


class TestMetadataUserOnly(AbstractDBTest):
    """Test metadata with only user_id (personal preferences)"""

    class_under_test = MetadataModel
    parent_entities = [
        ParentEntity(name="UserModel", foreign_key="user_id", test_class=TestUser)
    ]
    create_fields = {
        "user_id": "",  # Will be populated by parent_entities
        "team_id": None,
        "key": "user_preference",
        "value": "test_value",
    }
    update_fields = {
        "value": "updated_value",
    }


class TestMetadataTeamOnly(AbstractDBTest):
    """Test metadata with only team_id (team settings)"""

    class_under_test = MetadataModel
    parent_entities = [
        ParentEntity(name="TeamModel", foreign_key="team_id", test_class=TestTeam)
    ]
    create_fields = {
        "user_id": None,
        "team_id": "",  # Will be populated by parent_entities
        "key": "team_setting",
        "value": "test_value",
    }
    update_fields = {
        "value": "updated_value",
    }


class TestMetadataUserTeam(AbstractDBTest):
    """Test metadata with both user_id and team_id (team-specific user data)"""

    class_under_test = MetadataModel
    parent_entities = [
        ParentEntity(name="UserModel", foreign_key="user_id", test_class=TestUser),
        ParentEntity(name="TeamModel", foreign_key="team_id", test_class=TestTeam),
    ]
    create_fields = {
        "user_id": "",  # Will be populated by parent_entities
        "team_id": "",  # Will be populated by parent_entities
        "key": "user_team_preference",
        "value": "test_value",
    }
    update_fields = {
        "value": "updated_value",
    }


class TestRole(AbstractDBTest):
    class_under_test = RoleModel
    create_fields = {
        "name": "test_role",
        "friendly_name": "Test RoleModel",
        "mfa_count": 1,
        "password_change_frequency_days": 90,
    }
    update_fields = {
        "friendly_name": "Updated Test RoleModel",
        "mfa_count": 2,
        "password_change_frequency_days": 180,
    }
    unique_fields = ["name"]


class TestUserTeam(AbstractDBTest):
    class_under_test = UserTeamModel
    parent_entities = [
        ParentEntity(name="UserModel", foreign_key="user_id", test_class=TestUser),
        ParentEntity(name="TeamModel", foreign_key="team_id", test_class=TestTeam),
    ]
    create_fields = {
        "user_id": "",  # Will be populated by parent_entities
        "team_id": "",  # Will be populated by parent_entities
        "role_id": env("USER_ROLE_ID"),
        "enabled": True,
    }
    update_fields = {
        "enabled": False,
    }


class TestInvitation(AbstractDBTest):
    class_under_test = InvitationModel
    create_fields = {
        "team_id": None,  # Will be populated in setup
        "role_id": env("USER_ROLE_ID"),  # Will be populated in setup
        "user_id": None,  # Will be populated in setup
        "code": lambda: f"test_invitation_code_{uuid.uuid4()}",
        "max_uses": 5,
    }
    update_fields = {
        "code": lambda: f"updated_invitation_code_{uuid.uuid4()}",
        "max_uses": 10,
    }
    unique_fields = ["code"]


class TestInvitee(AbstractDBTest):
    class_under_test = InviteeModel
    parent_entities = [
        ParentEntity(
            name="InvitationModel",
            foreign_key="invitation_id",
            test_class=TestInvitation,
        )
    ]
    create_fields = {
        "invitation_id": None,  # Will be populated in setup
        "user_id": None,  # Will be populated in setup
        "email": faker.unique.email,
    }
    update_fields = {
        "accepted_at": datetime.now(),
    }


class TestPermission(AbstractDBTest):
    class_under_test = PermissionModel
    create_fields = {
        "resource_type": "invitations",
        "resource_id": "",  # Will be populated in setup
        "can_view": True,
        "can_edit": False,
        "can_delete": False,
        "can_share": False,
    }
    update_fields = {
        "can_edit": True,
        "can_share": True,
    }
    parent_entities = [
        ParentEntity(
            name="InvitationModel",
            foreign_key="resource_id",
            test_class=TestInvitation,
        )
    ]


class TestFailedLoginAttempt(AbstractDBTest):
    class_under_test = FailedLoginAttemptModel
    parent_entities = [
        ParentEntity(name="UserModel", foreign_key="user_id", test_class=TestUser)
    ]
    create_fields = {
        "user_id": "",  # Will be populated by parent_entities
        "ip_address": "127.0.0.1",
    }
    update_fields = {
        "ip_address": "192.168.1.1",
    }


class TestSession(AbstractDBTest):
    class_under_test = SessionModel
    parent_entities = [
        ParentEntity(name="UserModel", foreign_key="user_id", test_class=TestUser)
    ]
    create_fields = {
        "user_id": "",  # Will be populated by parent_entities
        "session_key": faker.uuid4,
        "jwt_issued_at": datetime.now(),
        "last_activity": datetime.now(),
        "expires_at": datetime.now() + timedelta(days=1),
        "is_active": True,
    }
    update_fields = {
        "refresh_token_hash": "updated_refresh_token",
        "last_activity": datetime.now(),
        "is_active": False,
    }
    unique_fields = ["session_key"]


class TestRateLimitPolicy(AbstractDBTest):
    class_under_test = RateLimitPolicyModel
    create_fields = {
        "name": "test_rate_limit",
        "resource_pattern": "api/v1/test/*",
        "window_seconds": 60,
        "max_requests": 100,
        "scope": "UserModel",
    }
    update_fields = {
        "window_seconds": 120,
        "max_requests": 200,
        "scope": "ip",
    }
    unique_fields = ["name"]
