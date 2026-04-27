import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from faker import Faker
from fastapi import HTTPException
from loguru import logger

from AbstractTest import (
    CategoryOfTest,
    ClassOfTestsConfig,
    ParentEntity,
    SkipReason,
    SkipThisTest,
)
from lib.Environment import env
from logic.AbstractBLLTest import AbstractBLLTest
from logic.BLL_Auth import (
    InvitationManager,
    InviteeManager,
    MetadataManager,
    PermissionManager,
    RoleManager,
    SessionManager,
    TeamManager,
    UserCredentialManager,
    UserManager,
    UserMetadataManager,
    UserTeamManager,
)

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(categories=[CategoryOfTest.LOGIC])

# Initialize faker for generating test data once
faker = Faker()


def create_test_user(model_registry, **kwargs):
    """Helper function to create test users using the static register method."""
    # Generate default user data
    user_data = {
        "email": kwargs.get("email", f"test_{uuid.uuid4().hex[:8]}@example.com"),
        "username": kwargs.get("username", f"test_{uuid.uuid4().hex[:8]}"),
        "display_name": kwargs.get("display_name", faker.name()),
        "first_name": kwargs.get("first_name", faker.first_name()),
        "last_name": kwargs.get("last_name", faker.last_name()),
        "password": kwargs.get("password", "Test1234!"),
    }

    # Override with any provided kwargs
    user_data.update(kwargs)

    # Register the user using the static method
    return UserManager.register(user_data, model_registry)


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


class TestUserManager(AbstractBLLTest):
    class_under_test = UserManager
    create_fields = {
        "email": lambda: f"test_{faker.word()}_{faker.random_int()}@example.com",
        "username": lambda: f"user_{faker.word()}_{faker.random_int()}",
        "display_name": lambda: faker.name(),
        "first_name": lambda: faker.first_name(),
        "last_name": lambda: faker.last_name(),
        "password": "Test1234!",
    }
    update_fields = {
        "display_name": f"Updated {faker.name()}",
        "first_name": f"Updated {faker.first_name()}",
        "last_name": f"Updated {faker.last_name()}",
    }
    unique_fields = ["username", "email"]

    _skip_tests = [
        SkipThisTest(
            name="test_search",
            reason=SkipReason.IRRELEVANT,
            details="Users cannot be searched",
        ),
        SkipThisTest(
            name="test_batch_update",
            reason=SkipReason.IRRELEVANT,
            details="The only user that can update a user is themself",
        ),
        SkipThisTest(
            name="test_batch_delete",
            reason=SkipReason.IRRELEVANT,
            details="The only user that can delete a user is themself",
        ),
    ]

    @classmethod
    def create_for_parent_entity(cls, model_registry, **kwargs):
        """Class method to create a user for use as a parent entity in other tests."""
        return create_test_user(model_registry, **kwargs)

    def _create(
        self, user_id, team_id, key="default", server=None, model_registry=None
    ):
        """Override _create to use the static register method."""
        # Get model_registry from server if not provided
        if model_registry is None and server is not None:
            model_registry = server.app.state.model_registry
        elif model_registry is None and hasattr(self, "server"):
            model_registry = self.server.app.state.model_registry

        # Build entities to get the fields
        entity_data = self.build_entities(
            server if server else getattr(self, "server", None),
            user_id=user_id,
            team_id=team_id,
            unique_fields=getattr(self, "unique_fields", []),
        )[
            0
        ]  # Get the first (and only) entity from the list

        # Register the user using the static method
        entity = UserManager.register(entity_data, model_registry)

        # Track the entity
        self.tracked_entities[key] = entity

        return entity

    # @pytest.mark.dependency(name="test_create")
    def test_create(self, admin_a, team_a, server, model_registry):
        """Override: Test creating a user entity."""
        # Ensure the base create logic is called with necessary context
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            key="create",
            server=server,
            model_registry=model_registry,
        )
        self._create_assert("create")

    # @pytest.mark.dependency(depends=["test_create"])
    def test_delete(self, admin_a, team_a, server, model_registry):
        """Override: Test deleting a user entity."""
        # Ensure a specific entity is created for this delete test
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "delete",
            server=server,
            model_registry=model_registry,
        )
        # Call the base delete logic with necessary context
        entity_id = self._delete(
            self.tracked_entities["delete"].id, model_registry=model_registry
        )
        # Assert deletion using the base assertion logic
        self._delete_assert(
            self.tracked_entities["delete"].id,
            self.tracked_entities["delete"].id,
            model_registry=model_registry,
        )

    # User records self-set ``created_by_user_id`` to the new user's id (see
    # AbstractDatabaseEntity.create), so admin_a has no permission claim on
    # the resulting record. Override the inherited test to drive the update
    # as the new user themselves, mirroring the DB-layer override pattern.
    def test_update(self, admin_a, team_a, server, model_registry):
        """Override: Test updating a user entity as the user themselves."""
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id,
            team_a.id,
            "update",
            server=server,
            model_registry=model_registry,
        )
        new_user_id = self.tracked_entities["update"].id
        updated_fields = self._update(
            new_user_id, team_a.id, model_registry=model_registry
        )
        self._update_assert("update_result", updated_fields)

    def test_create_closed(self, server, model_registry):
        """Test user registration fails when REGISTRATION_MODE is 'closed'."""
        from fastapi import HTTPException

        # Store original environment value
        original_value = os.environ.get("REGISTRATION_MODE")

        try:
            # Set environment variable to test real functionality
            os.environ["REGISTRATION_MODE"] = "closed"

            # Reimport settings to pick up the environment change
            import importlib

            from lib import Environment

            importlib.reload(Environment)

            user_data = {
                "email": f"test_closed_{faker.random_int()}@example.com",
                "password": "Test1234!",
                "first_name": faker.first_name(),
                "last_name": faker.last_name(),
            }

            # Registration should fail with 403
            with pytest.raises(HTTPException) as exc_info:
                UserManager.register(user_data, model_registry)

            assert exc_info.value.status_code == 403
            assert "User registration is currently closed" in exc_info.value.detail

        finally:
            # Restore original environment value
            if original_value is not None:
                os.environ["REGISTRATION_MODE"] = original_value
            else:
                os.environ.pop("REGISTRATION_MODE", None)
            # Reload settings to restore original state
            importlib.reload(Environment)

    def test_create_invite_only(self, server, model_registry):
        """Test user registration fails when REGISTRATION_MODE is 'invite'."""
        from fastapi import HTTPException

        # Store original environment value
        original_value = os.environ.get("REGISTRATION_MODE")

        try:
            # Set environment variable to test real functionality
            os.environ["REGISTRATION_MODE"] = "invite"

            # Reimport settings to pick up the environment change
            import importlib

            from lib import Environment

            importlib.reload(Environment)

            user_data = {
                "email": f"test_invite_{faker.random_int()}@example.com",
                "password": "Test1234!",
                "first_name": faker.first_name(),
                "last_name": faker.last_name(),
            }

            # Registration should fail with 403
            with pytest.raises(HTTPException) as exc_info:
                UserManager.register(user_data, model_registry)

            assert exc_info.value.status_code == 403
            assert "User registration requires an invitation" in exc_info.value.detail

        finally:
            # Restore original environment value
            if original_value is not None:
                os.environ["REGISTRATION_MODE"] = original_value
            else:
                os.environ.pop("REGISTRATION_MODE", None)
            # Reload settings to restore original state
            importlib.reload(Environment)
        # TODO we also need these tests in invitation acceptance
        # TODO we also need these tests in registration EP tests
        # TODO we also need these tests in invitation acceptance EP tests


class TestTeamManager(AbstractBLLTest):
    class_under_test = TeamManager
    create_fields = {
        "name": f"Test Team {faker.word()}",
        "description": faker.sentence(),
        "encryption_key": faker.uuid4(),
    }
    update_fields = {
        "name": f"Updated Team {faker.word()}",
        "description": f"Updated {faker.sentence()}",
    }
    unique_fields = ["name"]

    def test_create_team_with_empty_name_fails(self, admin_a, model_registry):
        """Test that team creation with empty name fails."""
        with pytest.raises(ValueError, match="Team name cannot be empty"):
            manager = self.class_under_test(
                requester_id=admin_a.id, model_registry=model_registry
            )
            manager.create(name="", description="Test description")

    def test_create_team_with_whitespace_name_fails(self, admin_a, model_registry):
        """Test that team creation with whitespace-only name fails."""
        with pytest.raises(ValueError, match="Team name cannot be empty"):
            manager = self.class_under_test(
                requester_id=admin_a.id, model_registry=model_registry
            )
            manager.create(name="   ", description="Test description")

    def test_create_team_with_spaced_name_trims(self, admin_a, model_registry):
        """Test that team creation with leading/trailing spaces trims the name."""
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        team_name_with_spaces = "  Test Team Name  "
        expected_trimmed_name = "Test Team Name"

        team = manager.create(
            name=team_name_with_spaces, description="Test description"
        )
        assert team.name == expected_trimmed_name

    def test_update_team_with_empty_name_fails(self, admin_a, team_a, model_registry):
        """Test that team update with empty name fails."""
        with pytest.raises(ValueError, match="Team name cannot be empty"):
            manager = self.class_under_test(
                requester_id=admin_a.id, model_registry=model_registry
            )
            manager.update(id=team_a.id, name="")

    def test_update_team_with_whitespace_name_fails(
        self, admin_a, team_a, model_registry
    ):
        """Test that team update with whitespace-only name fails."""
        with pytest.raises(ValueError, match="Team name cannot be empty"):
            manager = self.class_under_test(
                requester_id=admin_a.id, model_registry=model_registry
            )
            manager.update(id=team_a.id, name="   ")


class TestRoleManager(AbstractBLLTest):
    class_under_test = RoleManager
    create_fields = {
        "name": f"Test Role {faker.word()}",
        "friendly_name": f"Test Friendly Role {faker.word()}",
        "mfa_count": 1,
        "password_change_frequency_days": 90,
    }
    update_fields = {
        "friendly_name": f"Updated Friendly Role {faker.word()}",
        "mfa_count": 2,
        "password_change_frequency_days": 180,
    }
    unique_fields = ["name"]
    parent_entities = [
        ParentEntity(name="team", foreign_key="team_id", test_class=TestTeamManager),
    ]

    # @pytest.mark.dependency(depends=["test_create"])
    def test_list(self, admin_a, team_a, server, model_registry):
        """Test listing role entities with and without team context."""
        self.server = server
        self.model_registry = model_registry
        # Create roles without team context
        self._create(
            admin_a.id, None, "list_1", server=server, model_registry=model_registry
        )
        self._create(
            admin_a.id, None, "list_2", server=server, model_registry=model_registry
        )

        # Create roles with team context
        self._create(
            admin_a.id,
            team_a.id,
            "list_3",
            server=server,
            model_registry=model_registry,
        )
        self._create(
            admin_a.id,
            team_a.id,
            "list_4",
            server=server,
            model_registry=model_registry,
        )

        # List all roles (should see both team and non-team roles)
        self._list(admin_a.id, None, model_registry=model_registry)
        self._list_assert("list_result")

        # List team-specific roles
        self._list(admin_a.id, team_a.id, model_registry=model_registry)
        self._list_assert("list_result")  # Will check team-specific roles


class TestMetadataManager(AbstractBLLTest):
    """Test the unified MetadataManager"""

    class_under_test = MetadataManager
    create_fields = {
        "user_id": None,  # Will be set in test_create
        "key": f"test_key_{faker.word()}",
        "value": faker.sentence(),
    }
    update_fields = {
        "value": "Updated Test Value",
    }
    unique_fields = ["key"]

    def test_create(self, admin_a, team_a, server, model_registry):
        """Override: Test creating a metadata entity."""
        # Update create_fields with actual user_id
        self.create_fields["user_id"] = admin_a.id
        # Call parent create method
        self._create(
            admin_a.id, team_a.id, server=server, model_registry=model_registry
        )
        self._create_assert("create")

    def test_create_user_metadata(self, admin_a, server, model_registry):
        """Test creating metadata for a user"""
        mgr = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        metadata = mgr.create(user_id=admin_a.id, key="theme", value="dark")
        assert metadata.user_id == admin_a.id
        assert metadata.team_id is None
        assert metadata.key == "theme"
        assert metadata.value == "dark"

    def test_create_team_metadata(self, admin_a, team_a, server, model_registry):
        """Test creating metadata for a team"""
        mgr = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        metadata = mgr.create(
            team_id=team_a.id, key="billing_email", value="billing@example.com"
        )
        assert metadata.team_id == team_a.id
        assert metadata.user_id is None
        assert metadata.key == "billing_email"
        assert metadata.value == "billing@example.com"

    def test_create_user_team_metadata(self, admin_a, team_a, server, model_registry):
        """Test creating metadata with both user and team"""
        mgr = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        metadata = mgr.create(
            user_id=admin_a.id, team_id=team_a.id, key="role_in_team", value="admin"
        )
        assert metadata.user_id == admin_a.id
        assert metadata.team_id == team_a.id
        assert metadata.key == "role_in_team"
        assert metadata.value == "admin"

    def test_create_without_ids_fails(self, admin_a, server, model_registry):
        """Test that creating metadata without user_id or team_id fails"""
        mgr = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        with pytest.raises(HTTPException) as exc_info:
            mgr.create(key="test", value="value")
        assert exc_info.value.status_code == 400
        assert "Either user_id or team_id must be provided" in str(
            exc_info.value.detail
        )

    def test_set_preference(self, admin_a, server, model_registry):
        """Test set_preference method"""
        # Create a disposable test user
        user_data = {
            "email": f"test_metadata_set_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_metadata_set_user_{uuid.uuid4().hex[:8]}",
            "password": "TestPass123!",
        }
        test_user = UserManager.register(user_data, model_registry)

        mgr = self.class_under_test(
            requester_id=test_user.id, model_registry=model_registry
        )
        # Set new preference
        result = mgr.set_preference("language", "en", user_id=test_user.id)
        assert result["key"] == "language"
        assert result["value"] == "en"
        assert result["action"] == "created"

        # Update existing preference
        result = mgr.set_preference("language", "es", user_id=test_user.id)
        assert result["key"] == "language"
        assert result["value"] == "es"
        assert result["action"] == "updated"

    def test_get_preference(self, admin_a, server, model_registry):
        """Test get_preference method"""
        # Create a disposable test user
        user_data = {
            "email": f"test_metadata_get_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_metadata_get_user_{uuid.uuid4().hex[:8]}",
            "password": "TestPass123!",
        }
        test_user = UserManager.register(user_data, model_registry)

        mgr = self.class_under_test(
            requester_id=test_user.id, model_registry=model_registry
        )
        # Set a preference
        mgr.set_preference("timezone", "UTC", user_id=test_user.id)

        # Get the preference
        value = mgr.get_preference("timezone", user_id=test_user.id)
        assert value == "UTC"

        # Get non-existent preference
        value = mgr.get_preference("nonexistent", user_id=test_user.id)
        assert value is None


class TestUserTeamManager(AbstractBLLTest):
    class_under_test = UserTeamManager
    create_fields = {
        "enabled": True,
    }
    update_fields = {
        "enabled": False,
    }
    parent_entities = [
        ParentEntity(name="user", foreign_key="user_id", test_class=TestUserManager),
        ParentEntity(name="team", foreign_key="team_id", test_class=TestTeamManager),
        ParentEntity(name="role", foreign_key="role_id", test_class=TestRoleManager),
    ]


class TestSessionManager(AbstractBLLTest):
    class_under_test = SessionManager
    is_system_entity = False
    create_fields = {
        "session_key": lambda: f"session_{uuid.uuid4().hex}",
        "jwt_issued_at": lambda: datetime.now(timezone.utc),
        "last_activity": lambda: datetime.now(timezone.utc),
        "expires_at": lambda: datetime.now(timezone.utc) + timedelta(hours=24),
        "user_id": None,  # Will be set by build_entities from parameter
    }
    update_fields = {
        "last_activity": datetime(2025, 12, 25, 12, 0, 0),
        "expires_at": datetime(2025, 12, 31, 23, 59, 59),
    }

    unique_fields = ["session_key"]

    def test_search(
        self, admin_a, team_a, server, model_registry, search_field, search_operator
    ):
        """
        Override search test to handle session-specific user ownership requirements.

        Sessions have a special ownership model where they must belong to the user who creates them.
        The base AbstractBLLTest.test_search method cannot handle this requirement because:

        1. Sessions require a valid user_id field that references the session owner
        2. The session creator (requester_id) must be the same as the session owner (user_id)
        3. Permission validation in SessionManager.create_validation requires the user to exist

        This override ensures that:
        - The test session is created with user_id set to admin_a.id (the test user)
        - The session is created by admin_a (proper ownership for permission checks)
        - Search operations work correctly within the user's permission context

        Without this override, the base test would fail because:
        - user_id would be None (causing validation errors)
        - Permission checks would fail due to mismatched ownership
        - The session creation would be rejected by create_validation

        Args:
            admin_a: Test user fixture who will own the session
            team_a: Test team fixture for team context
            server: Test server fixture
            model_registry: Model registry fixture
            search_field: Field name to search on (parameterized)
            search_operator: Search operator to use (parameterized)

        This test validates that all searchable session fields work correctly with proper
        user ownership and permission context.
        """
        self.server = server
        self.model_registry = model_registry

        # Create entity if not already created
        if not hasattr(self, "_search_test_entity"):
            entity_data = self._get_unique_entity_data()
            requester_id = env("SYSTEM_ID") if self.is_system_entity else admin_a.id

            # CRITICAL: Set the user_id to the requester (admin_a)
            entity_data["user_id"] = admin_a.id

            # Debug: Print entity data to see what's None
            logger.debug(f"DEBUG: entity_data before create: {entity_data}")
            for key, value in entity_data.items():
                if value is None:
                    logger.debug(f"DEBUG: WARNING - {key} is None!")

            manager = self.class_under_test(
                requester_id=requester_id,
                target_team_id=team_a.id,
                model_registry=model_registry,
            )
            self._search_test_entity = manager.create(**entity_data)
            self.tracked_entities["search_target"] = self._search_test_entity

        entity = self._search_test_entity

        # Convert entity to dict
        entity_dict = (
            entity.model_dump() if hasattr(entity, "model_dump") else entity.__dict__
        )

        # Get field value
        field_value = entity_dict.get(search_field)
        if field_value is None:
            pytest.skip(f"Field {search_field} is None in test entity")

        # Get search value for operator
        search_value = self.get_search_value_for_operator(field_value, search_operator)

        # Construct search criteria
        if search_operator == "value":
            # Direct value syntax (implicit eq)
            search_criteria = {search_field: search_value}
        else:
            # Nested operator syntax
            search_criteria = {search_field: {search_operator: search_value}}

        # Perform search
        requester_id = env("SYSTEM_ID") if self.is_system_entity else admin_a.id
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        results = manager.search(**search_criteria)

        # Verify entity is found
        result_ids = [r.id for r in results]
        assert entity.id in result_ids, (
            f"Entity not found when searching {search_field} with operator '{search_operator}' "
            f"and value '{search_value}' (type: {type(search_value).__name__}). Found {len(results)} results."
        )


class TestInvitationManager(AbstractBLLTest):
    """
    Comprehensive tests for InvitationManager covering all three invitation acceptance scenarios:

    1. Direct invite to existing user (via invitee_id):
       - test_direct_invitation_to_existing_user_comprehensive()
       - test_unified_invitation_acceptance_via_invitee_id()

    2. Public invitation code (via invitation_code):
       - test_public_invitation_code_comprehensive()
       - test_unified_invitation_acceptance_via_code()

    3. Direct invite to non-existing user by email (registration-time acceptance):
       - test_registration_time_invitation_acceptance_comprehensive()
       - test_registration_time_invitation_acceptance()

    Additional supporting tests:
    - test_create_invitation_code() - Creating invitations with codes
    - test_create_invitation_email() - Adding emails to invitations
    - test_code_generation_behavior() - Code generation logic
    - test_create_app_level_invitation() - App-level invitations
    - test_unified_acceptance_validation() - Validation logic
    - test_invalid_invitation_acceptance_scenarios() - Error scenarios
    """

    class_under_test = InvitationManager
    create_fields = {
        "code": lambda: f"TEST_{uuid.uuid4().hex[:8].upper()}",
        "max_uses": 5,
        "user_id": None,  # Will be set by parent entity
        "team_id": None,  # Will be set by parent entity
        "role_id": None,  # Will be set by parent entity
    }
    update_fields = {
        "max_uses": 10,
    }
    parent_entities = [
        ParentEntity(name="user", foreign_key="user_id", test_class=TestUserManager),
        ParentEntity(name="team", foreign_key="team_id", test_class=TestTeamManager),
        ParentEntity(name="role", foreign_key="role_id", test_class=TestRoleManager),
    ]

    def _create_parent_entities_for_search(self, requester_id, team_id, model_registry):
        """Override parent entity creation to ensure access to created teams."""
        parent_data = {}
        if not hasattr(self, "parent_entities") or not self.parent_entities:
            return parent_data

        for parent_config in self.parent_entities:
            if parent_config.name == "team":
                # Create a team that the requester has access to and add them as a member
                from lib.Environment import env
                from logic.BLL_Auth import TeamManager, UserTeamManager

                with TeamManager(
                    requester_id=requester_id,
                    model_registry=model_registry,
                ) as team_manager:
                    team = team_manager.create(
                        name=f"Test Team {requester_id}",
                        description="Test team for invitation search tests",
                    )
                    parent_data[parent_config.foreign_key] = team.id

                # Add the requester as a team member so they can see the team
                with UserTeamManager(
                    requester_id=requester_id,
                    model_registry=model_registry,
                ) as user_team_manager:
                    user_team_manager.create(
                        user_id=requester_id,
                        team_id=team.id,
                        role_id=env("ADMIN_ROLE_ID"),
                    )
            elif parent_config.name == "user":
                parent_data[parent_config.foreign_key] = requester_id
            elif parent_config.name == "role":
                parent_data[parent_config.foreign_key] = env("USER_ROLE_ID")
            else:
                # Fall back to default behavior for other parent entities
                parent_test_class = parent_config.test_class
                if callable(parent_test_class):
                    try:
                        parent_instance = parent_test_class()
                    except:
                        parent_instance = parent_test_class
                else:
                    parent_instance = parent_test_class

                parent_entity_data = parent_instance._get_unique_entity_data()
                parent_manager_class = (
                    parent_test_class.class_under_test
                    if hasattr(parent_test_class, "class_under_test")
                    else parent_instance.class_under_test
                )

                with parent_manager_class(
                    requester_id=requester_id,
                    target_team_id=team_id,
                    model_registry=model_registry,
                ) as parent_manager:
                    parent_entity = parent_manager.create(**parent_entity_data)
                    parent_data[parent_config.foreign_key] = parent_entity.id

        return parent_data

    def _get_entity_data_for_search(
        self, admin_a, team_a, server, requester_id, model_registry
    ):
        """Override to use the team created by parent entity setup for invitation search tests."""
        # Get the standard entity data first
        entity_data = super()._get_entity_data_for_search(
            admin_a, team_a, server, requester_id, model_registry
        )

        # If this has a team_id from parent entity creation, we need to ensure the manager
        # uses the same team_id as target_team_id for proper context
        if "team_id" in entity_data and entity_data["team_id"]:
            # Store the team_id for use in the manager context
            self._search_team_id = entity_data["team_id"]

        return entity_data

    def test_search(
        self, admin_a, team_a, server, model_registry, search_field, search_operator
    ):
        """Override search test to use the correct team context for invitations."""
        self.server = server
        self.model_registry = model_registry

        # Create entity if not already created
        if not hasattr(self, "_search_test_entity"):
            requester_id = admin_a.id  # InvitationManager is not a system entity
            entity_data = self._get_entity_data_for_search(
                admin_a, team_a, server, requester_id, model_registry
            )

            # Use the team from entity_data instead of team_a if available
            target_team_id = getattr(self, "_search_team_id", team_a.id)

            with self.class_under_test(
                requester_id=requester_id,
                target_team_id=target_team_id,
                model_registry=model_registry,
            ) as manager:
                self._search_test_entity = manager.create(**entity_data)
                self.tracked_entities["search_target"] = self._search_test_entity

        # Continue with the standard search logic
        entity = self._search_test_entity

        # Get field value and perform search
        field_value = getattr(entity, search_field, None)
        if field_value is None:
            pytest.skip(f"Entity has no field '{search_field}' or field is None")
            return

        search_params = {search_field: {search_operator: field_value}}

        target_team_id = getattr(self, "_search_team_id", team_a.id)
        with self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        ) as manager:
            results = manager.search(search_params)

        # Verify results
        assert (
            results is not None
        ), f"Search returned None for {search_field} {search_operator} {field_value}"
        assert (
            len(results) > 0
        ), f"No results found for {search_field} {search_operator} {field_value}"

        # Verify our entity is in the results
        result_ids = [r.id for r in results]
        assert (
            entity.id in result_ids
        ), f"Created entity not found in search results for {search_field} {search_operator} {field_value}"

    def test_create_invitation_code(self, admin_a, team_a, server, model_registry):
        """Test creating an invitation with a specific code."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create our own test entities to avoid mutating shared fixtures
        test_user_data = {
            "email": f"test_inv_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_inv_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Invitation User",
            "password": "TestPassword123!",
        }
        test_user = UserManager.register(test_user_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_user.id,
            model_registry=model_registry,
        )
        test_team = team_manager.create(
            name="Test Invitation Team",
            description="Team for invitation testing",
        )

        test_code = f"TESTCODE_{uuid.uuid4().hex[:8].upper()}"

        manager = self.class_under_test(
            requester_id=test_user.id,
            target_team_id=test_team.id,
            model_registry=model_registry,
        )
        invitation_data = {
            "code": test_code,
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_user.id,
            "max_uses": 5,
        }

        invitation = manager.create(**invitation_data)
        self.tracked_entities["create_code"] = invitation

        assert invitation is not None
        assert invitation.code == test_code
        assert invitation.team_id == test_team.id
        assert invitation.user_id == test_user.id

        # Test that invitation link can be generated
        link = manager.generate_invitation_link(test_code)
        assert test_code in link
        assert link.startswith(env("APP_URI"))

    def test_create_invitation_email(self, admin_a, team_a, server, model_registry):
        """Test creating an invitation and adding an email to it."""
        from logic.BLL_Auth import TeamManager, UserManager

        self.server = server
        self.model_registry = model_registry
        # Create our own test entities to avoid mutating shared fixtures
        test_user_data = {
            "email": f"test_inv_email_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_inv_email_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Invitation Email User",
            "password": "TestPassword123!",
        }
        test_user = UserManager.register(test_user_data, model_registry)

        team_manager = TeamManager(
            requester_id=test_user.id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test Invitation Email Team",
            description="Team for invitation email testing",
        )

        test_email = f"invitee_{uuid.uuid4().hex[:8]}@example.com"

        manager = self.class_under_test(
            requester_id=test_user.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_user.id,
            "max_uses": 10,
        }
        invitation = manager.create(**invitation_data)
        self.tracked_entities["create_email"] = invitation

        # Add invitee to the invitation
        invitee_result = manager.add_invitee(invitation.id, test_email)

        assert invitee_result is not None
        assert invitee_result["invitation_id"] == invitation.id
        assert invitee_result["invitation_code"] == invitation.code
        assert invitee_result["email"] == test_email
        # Team-based invitations should have invitation links since they have codes
        assert "invitation_link" in invitee_result
        assert invitation.code in invitee_result["invitation_link"]

    def test_create_invitation_email_nonexistent_user(
        self, admin_a, team_a, server, model_registry
    ):
        self.server = server
        self.model_registry = model_registry
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        invitation_data = {
            "team_id": team_a.id,
            "role_id": env("USER_ROLE_ID"),
            "email": faker.email(),
        }
        invitation = manager.create(**invitation_data)

        assert invitation is not None

        Invitee = manager.Invitee_manager.get(invitation_id=invitation.id)

        assert Invitee is not None
        assert Invitee.created_by_user_id == admin_a.id

        # try inviting the same email to the same team again
        # create invitation should raise exception
        with pytest.raises(HTTPException) as exc_info:
            invitation = manager.create(**invitation_data)
        assert exc_info.value.status_code == 400
        assert "already invited" in str(exc_info.value.detail).lower()

    def test_create_invitation_email_existing_user(
        self, admin_a, team_a, user_c, server, model_registry
    ):
        self.server = server
        self.model_registry = model_registry
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=model_registry
        )
        invitation_data = {
            "team_id": team_a.id,
            "role_id": env("USER_ROLE_ID"),
            "email": user_c.email,
        }
        invitation = manager.create(**invitation_data)

        assert invitation is not None

        Invitee = manager.Invitee_manager.get(invitation_id=invitation.id)

        assert Invitee is not None
        assert Invitee.created_by_user_id == admin_a.id
        assert Invitee.user_id == user_c.id

    def test_invitation_acceptance_at_registration_time(self, server, model_registry):
        """Test accepting an invitation during user registration (user story #3)."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_reg_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_reg_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Registration Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_admin.id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test Registration Team",
            description="Team for registration invitation testing",
        )

        # Create an invitation
        invitation_code = f"REGCODE_{uuid.uuid4().hex[:8].upper()}"
        manager = self.class_under_test(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": invitation_code,
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin.id,
            "max_uses": 5,
        }
        invitation = manager.create(**invitation_data)
        logger.debug(f"Created invitation with team_id: {invitation.team_id}")
        logger.debug(f"test_team.id: {test_team.id}")
        assert (
            invitation.team_id == test_team.id
        ), f"Invitation has wrong team_id: {invitation.team_id} != {test_team.id}"

        # Create a user with invitation_code during registration
        user_data = {
            "email": f"new_user_reg_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"new_user_reg_{uuid.uuid4().hex[:8]}",
            "display_name": "New Registration User",
            "password": "TestPassword123!",
            "invitation_code": invitation_code,  # Include invitation code
        }
        user = UserManager.register(user_data, self.model_registry)

        # Verify user was created
        assert user is not None
        assert user.email == user_data["email"]

        # Debug: Check what invitation was used
        logger.debug(f"User registered with email: {user.email}")

        # Store user ID to avoid detached session issues
        user_id = user.id
        team_id = test_team.id

        # Verify user was added to the team using a fresh manager
        user_team_manager = UserTeamManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        user_teams = user_team_manager.list(user_id=user_id)
        team_memberships = [ut for ut in user_teams if ut.team_id == team_id]
        assert len(team_memberships) == 1
        assert team_memberships[0].role_id == env("USER_ROLE_ID")
        assert team_memberships[0].enabled is True

        # Verify invitation metadata was added using a fresh manager
        metadata_manager = UserMetadataManager(
            requester_id=env("ROOT_ID"),
            target_id=user_id,
            model_registry=self.model_registry,
        )
        metadata = metadata_manager.get_preferences()
        assert metadata.get("invitation_accepted") == "true"
        # Debug: log what we're comparing
        logger.debug(f"Expected team_id: {team_id}")
        logger.debug(
            f"Actual invitation_team_id in metadata: {metadata.get('invitation_team_id')}"
        )
        # logger.debug(f"team_a.id (from fixture): {team_a.id}")
        logger.debug(f"Full metadata: {metadata}")
        assert metadata.get("invitation_team_id") == team_id

        self.tracked_entities["registration_user"] = user

    def test_invitation_creation_direct_to_existing_user_comprehensive(
        self, admin_a, team_a, server, model_registry
    ):
        """Test comprehensive direct invitation flow to existing user (user story #1)."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_direct_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_direct_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Direct Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_admin.id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test Direct Team",
            description="Team for direct invitation testing",
        )

        # Create the existing user who will be invited
        existing_user_data = {
            "email": f"existing_direct_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"existing_direct_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Existing Direct User",
            "password": "TestPassword123!",
        }
        existing_user = UserManager.register(existing_user_data, self.model_registry)

        # Create an invitation and add the existing user's email as invitee
        manager = self.class_under_test(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin.id,
            "max_uses": 5,
        }
        invitation = manager.create(**invitation_data)

        # Add the existing user as an invitee
        invitee_result = manager.add_invitee(invitation.id, existing_user.email)
        assert invitee_result["email"] == existing_user.email
        assert invitee_result["invitation_code"] == invitation.code

        # Get the invitee ID for accepting via invitee_id
        invitees = manager.Invitee_manager.list(
            email=existing_user.email, invitation_id=invitation.id
        )
        assert len(invitees) == 1
        invitee_id = invitees[0].id

        # Test unified acceptance via invitee_id
        from logic.BLL_Auth import InvitationModel

        accept_data = InvitationModel.Accept(invitee_id=invitee_id)
        result = manager.accept_invitation_unified(accept_data, existing_user.id)

        assert result["success"] is True
        assert result["message"] == "Invitation accepted successfully via invitee ID"
        assert result["team_id"] == test_team.id
        assert result["role_id"] == env("USER_ROLE_ID")
        assert result["user_team_id"] is not None

        # Store IDs to avoid detached session issues
        existing_user_id = existing_user.id
        existing_user_email = existing_user.email
        test_team_id = test_team.id
        invitation_id = invitation.id

        # Verify the user is now a member of the team using a fresh manager
        user_team_manager = UserTeamManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        user_teams = user_team_manager.list(user_id=existing_user_id)
        team_memberships = [ut for ut in user_teams if ut.team_id == test_team_id]
        assert len(team_memberships) == 1
        assert team_memberships[0].role_id == env("USER_ROLE_ID")
        assert team_memberships[0].enabled is True

        # Verify the invitee record shows acceptance using a fresh manager
        invitee_manager = InviteeManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        updated_invitees = invitee_manager.list(
            email=existing_user_email, invitation_id=invitation_id
        )
        assert len(updated_invitees) == 1
        assert updated_invitees[0].accepted_at is not None
        assert updated_invitees[0].user_id == existing_user_id

    def test_invitation_list_invitees_bad_invitation_id(
        self, admin_a, server, model_registry
    ):
        """Test listing invitees with a bad team ID."""
        self.server = server
        self.model_registry = model_registry

        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=self.model_registry
        )
        # Attempt to list invitees with a bad invitation ID
        with pytest.raises(HTTPException) as exc_info:
            manager.Invitee_manager.list(invitation_id=str(uuid.uuid4()))

    def test_invitation_acceptance_at_registration_time_comprehensive(
        self, server, model_registry
    ):
        """Test comprehensive registration-time invitation acceptance (user story #3)."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_reg_comp_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_reg_comp_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Registration Comprehensive Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_admin.id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test Registration Comprehensive Team",
            description="Team for comprehensive registration invitation testing",
        )

        # Define the email that will be used for registration
        registration_email = f"reg_comp_user_{uuid.uuid4().hex[:8]}@example.com"

        # Create an invitation and add the future user's email as invitee
        invitation_code = f"REGCOMPCODE_{uuid.uuid4().hex[:8].upper()}"
        manager = self.class_under_test(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": invitation_code,
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin.id,
            "max_uses": 5,
        }
        invitation = manager.create(**invitation_data)

        # Pre-add the email as an invitee (simulating email invitation)
        invitee_result = manager.add_invitee(invitation.id, registration_email)
        assert invitee_result["email"] == registration_email

        # Verify the invitee exists but has not accepted yet
        invitee_manager = InviteeManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        pre_registration_invitees = invitee_manager.list(email=registration_email)
        assert len(pre_registration_invitees) == 1
        assert pre_registration_invitees[0].accepted_at is None
        assert pre_registration_invitees[0].user_id is None

        # Create a user with invitation_code during registration
        user_data = {
            "email": registration_email,
            "username": f"reg_comp_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Registration Comprehensive User",
            "password": "TestPassword123!",
            "invitation_code": invitation_code,  # Include invitation code
        }
        user = UserManager.register(user_data, self.model_registry)

        # Verify user was created
        assert user is not None
        assert user.email == registration_email

        # Store IDs to avoid detached session issues
        user_id = user.id
        test_team_id = test_team.id

        # Verify user was added to the team using a fresh manager
        user_team_manager = UserTeamManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        user_teams = user_team_manager.list(user_id=user_id)
        team_memberships = [ut for ut in user_teams if ut.team_id == test_team_id]
        assert len(team_memberships) == 1
        assert team_memberships[0].role_id == env("USER_ROLE_ID")
        assert team_memberships[0].enabled is True

        # Verify invitation metadata was added using a fresh manager
        metadata_manager = UserMetadataManager(
            requester_id=env("ROOT_ID"),
            target_id=user_id,
            model_registry=self.model_registry,
        )
        metadata = metadata_manager.get_preferences()
        assert metadata.get("invitation_accepted") == "true"
        # Debug: log what we're comparing
        logger.debug(f"Expected test_team_id: {test_team_id}")
        logger.debug(
            f"Actual invitation_team_id in metadata: {metadata.get('invitation_team_id')}"
        )
        # logger.debug(f"team_a.id (from fixture): {team_a.id}")
        logger.debug(f"Full metadata: {metadata}")
        assert metadata.get("invitation_team_id") == test_team_id

        # Verify the invitee record was updated to show acceptance
        invitee_manager = InviteeManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        post_registration_invitees = invitee_manager.list(email=registration_email)
        assert len(post_registration_invitees) == 1
        accepted_invitee = post_registration_invitees[0]
        assert accepted_invitee.accepted_at is not None
        assert accepted_invitee.user_id == user_id

        self.tracked_entities["registration_comprehensive_user"] = user

    def test_invitation_acceptance_public_code(
        self, admin_a, team_a, server, model_registry
    ):
        """Test comprehensive public invitation code acceptance (user story #2)."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_public_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_public_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Public Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_admin.id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test Public Team",
            description="Team for public invitation testing",
        )

        # Create a public invitation with shareable code
        invitation_code = f"PUBLICCODE_{uuid.uuid4().hex[:8].upper()}"
        manager = self.class_under_test(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": invitation_code,
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin.id,
            "max_uses": 10,
        }
        invitation = manager.create(**invitation_data)
        assert invitation.code == invitation_code

        # Create a user who will use the public code
        public_user_data = {
            "email": f"public_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"public_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Public User",
            "password": "TestPassword123!",
        }
        public_user = UserManager.register(public_user_data, self.model_registry)

        # Test unified acceptance via invitation code
        from logic.BLL_Auth import InvitationModel

        accept_data = InvitationModel.Accept(invitation_code=invitation_code)
        result = manager.accept_invitation_unified(accept_data, public_user.id)

        assert result["success"] is True
        assert result["message"] == "Invitation accepted successfully via code"
        assert result["team_id"] == test_team.id
        assert result["role_id"] == env("USER_ROLE_ID")
        assert result["user_team_id"] is not None

        # Store IDs to avoid detached session issues
        public_user_id = public_user.id
        public_user_email = public_user.email
        test_team_id = test_team.id
        invitation_id = invitation.id

        # Verify the user is now a member of the team using a fresh manager
        user_team_manager = UserTeamManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        user_teams = user_team_manager.list(user_id=public_user_id)
        team_memberships = [ut for ut in user_teams if ut.team_id == test_team_id]
        assert len(team_memberships) == 1
        assert team_memberships[0].role_id == env("USER_ROLE_ID")
        assert team_memberships[0].enabled is True

        # Verify an invitee record was created for the public user using a fresh manager
        invitee_manager = InviteeManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        invitees = invitee_manager.list(
            email=public_user_email, invitation_id=invitation_id
        )
        assert len(invitees) == 1
        assert invitees[0].accepted_at is not None
        assert invitees[0].user_id == public_user_id

        # Test that the invitation link works (using the stored invitation code)
        link_manager = InvitationManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        invitation_link = link_manager.generate_invitation_link(invitation_code)
        assert invitation_code in invitation_link
        assert invitation_link.startswith(env("APP_URI"))

    def test_code_generation_behavior(self, admin_a, team_a, server, model_registry):
        """Test that codes are generated correctly for different invitation types."""
        self.server = server
        self.model_registry = model_registry

        # Create test users for each invitation to avoid duplicates
        from conftest import create_user

        test_user_1 = create_user(server, email="test1@example.com")
        test_user_2 = create_user(server, email="test2@example.com")
        test_user_3 = create_user(server, email="test3@example.com")

        manager = self.class_under_test(
            requester_id=test_user_1.id,
            model_registry=self.model_registry,
        )
        # Test 1: App-level invitation should NOT have a code
        app_invitation_data = {
            "user_id": test_user_1.id,
            "max_uses": 10,
        }
        app_invitation = manager.create(**app_invitation_data)
        assert app_invitation.code is None
        assert app_invitation.team_id is None
        assert app_invitation.role_id is None

        # Test 2: Team-based invitation should have a code (auto-generated)
        team_invitation_data = {
            "team_id": team_a.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_user_2.id,
            "max_uses": 5,
        }
        team_invitation = manager.create(**team_invitation_data)
        assert team_invitation.code is not None
        assert len(team_invitation.code) == 8  # Should be 8 character code
        assert team_invitation.team_id == team_a.id
        assert team_invitation.role_id == env("USER_ROLE_ID")

        # Test 3: Team-based invitation with explicit code should use provided code
        explicit_code = f"EXPLICIT_{uuid.uuid4().hex[:8].upper()}"
        explicit_code_data = {
            "code": explicit_code,
            "team_id": team_a.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_user_3.id,
        }
        explicit_invitation = manager.create(**explicit_code_data)
        assert explicit_invitation.code == explicit_code

        self.tracked_entities["app_invitation"] = app_invitation
        self.tracked_entities["team_invitation"] = team_invitation
        self.tracked_entities["explicit_invitation"] = explicit_invitation
        self.tracked_entities["test_user_1"] = test_user_1
        self.tracked_entities["test_user_2"] = test_user_2
        self.tracked_entities["test_user_3"] = test_user_3

    def test_create_app_level_invitation(self, admin_a, team_a, server, model_registry):
        """Test creating an app-level invitation without team/role for referral purposes."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import UserManager

        # Create our own test user
        test_user_data = {
            "email": f"test_app_inv_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_app_inv_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Test App Invitation User",
            "password": "TestPassword123!",
        }
        test_user = UserManager.register(test_user_data, self.model_registry)

        manager = self.class_under_test(
            requester_id=test_user.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "user_id": test_user.id,
            "max_uses": 10,
        }

        invitation = manager.create(**invitation_data)
        self.tracked_entities["create_app_level"] = invitation

        assert invitation is not None
        assert invitation.team_id is None
        assert invitation.role_id is None
        assert invitation.code is None  # App-level invitations should not have codes
        assert invitation.user_id == test_user.id
        assert invitation.max_uses == 10

    def test_create_team_invitation_requires_both_team_and_role(
        self, admin_a, team_a, server, model_registry
    ):
        """Test that team invitations require both team_id and role_id."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import UserManager

        # Create our own test user
        test_user_data = {
            "email": f"test_validation_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_validation_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Validation User",
            "password": "TestPassword123!",
        }
        test_user = UserManager.register(test_user_data, self.model_registry)

        manager = self.class_under_test(
            requester_id=test_user.id,
            model_registry=self.model_registry,
        )
        # Test creating invitation with only team_id should fail
        with pytest.raises(
            HTTPException, match="team_id and role_id must both be provided"
        ):
            invitation_data = {
                "user_id": test_user.id,
                "team_id": team_a.id,
                # Missing role_id
            }
            manager.create(**invitation_data)

        # Test creating invitation with only role_id should fail
        with pytest.raises(
            HTTPException, match="team_id and role_id must both be provided"
        ):
            invitation_data = {
                "user_id": test_user.id,
                "role_id": env("USER_ROLE_ID"),
                # Missing team_id
            }
            manager.create(**invitation_data)

    def test_unified_invitation_acceptance_via_code(
        self, admin_a, team_a, server, model_registry
    ):
        """Test the unified invitation acceptance method via invitation code."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create test entities
        test_admin_data = {
            "email": f"test_unified_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_unified_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Unified Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_admin.id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test Unified Team",
            description="Team for unified invitation testing",
        )

        # Create an invitation
        invitation_code = f"UNIFIEDCODE_{uuid.uuid4().hex[:8].upper()}"
        manager = self.class_under_test(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": invitation_code,
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin.id,
            "max_uses": 5,
        }
        invitation = manager.create(**invitation_data)

        # Create a user to accept the invitation
        user_data = {
            "email": f"unified_accepter_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"unified_accepter_{uuid.uuid4().hex[:8]}",
            "display_name": "Unified Accepter",
            "password": "TestPassword123!",
        }
        user = UserManager.register(user_data, self.model_registry)

        # Test unified acceptance via code
        from logic.BLL_Auth import InvitationModel

        accept_data = InvitationModel.Accept(invitation_code=invitation_code)
        result = manager.accept_invitation_unified(accept_data, user.id)

        assert result["success"] is True
        assert result["message"] == "Invitation accepted successfully via code"
        assert result["team_id"] == test_team.id
        assert result["role_id"] == env("USER_ROLE_ID")
        assert result["user_team_id"] is not None

    def test_unified_invitation_acceptance_via_invitee_id(
        self, admin_a, team_a, server, model_registry
    ):
        """Test the unified invitation acceptance method via invitee ID."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create test entities
        test_admin_data = {
            "email": f"test_unified_invitee_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_unified_invitee_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Unified Invitee Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)
        test_admin_id = test_admin.id

        team_manager = TeamManager(
            requester_id=test_admin_id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test Unified Invitee Team",
            description="Team for unified invitee invitation testing",
        )
        test_team_id = test_team.id

        # Create a team-based invitation (will have a code auto-generated)
        invitation_manager = self.class_under_test(
            requester_id=test_admin_id,
            target_team_id=test_team_id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "team_id": test_team_id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin_id,
        }
        invitation = invitation_manager.create(**invitation_data)

        # Add specific invitee
        invitee_email = f"unified_invitee_{uuid.uuid4().hex[:8]}@example.com"
        invitee_result = invitation_manager.add_invitee(invitation.id, invitee_email)

        # Team-based invitations have codes and links
        assert invitee_result["invitation_link"] is not None
        assert invitee_result["invitation_code"] is not None
        assert invitation.code in invitee_result["invitation_link"]

        # Create user with matching email
        user_data = {
            "email": invitee_email,
            "username": f"unified_invitee_{uuid.uuid4().hex[:8]}",
            "display_name": "Unified Invitee",
            "password": "TestPassword123!",
        }
        user = UserManager.register(user_data, self.model_registry)
        user_id = user.id

        # Get the invitee ID
        invitee_manager = InviteeManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )
        invitees = invitee_manager.list(email=invitee_email)
        assert len(invitees) == 1
        invitee_id = invitees[0].id

        # Test unified acceptance via invitee ID using a fresh manager
        fresh_invitation_manager = self.class_under_test(
            requester_id=env("ROOT_ID"),
            target_team_id=test_team_id,
            model_registry=self.model_registry,
        )
        from logic.BLL_Auth import InvitationModel

        accept_data = InvitationModel.Accept(invitee_id=invitee_id)
        result = fresh_invitation_manager.accept_invitation_unified(
            accept_data, user_id
        )

        assert result["success"] is True
        assert result["message"] == "Invitation accepted successfully via invitee ID"
        assert result["team_id"] == test_team_id
        assert result["role_id"] == env("USER_ROLE_ID")
        assert result["user_team_id"] is not None

    def test_unified_invitation_decline_via_code(
        self, admin_a, team_a, server, model_registry
    ):
        """Test the unified invitation decline method via invitation code."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create test entities
        test_admin_data = {
            "email": f"test_unified_decline_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_unified_decline_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Unified Decline Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        with TeamManager(
            requester_id=test_admin.id,
            model_registry=self.model_registry,
        ) as team_manager:
            test_team = team_manager.create(
                name="Test Unified Decline Team",
                description="Team for unified decline invitation testing",
            )

        # Create an invitation
        invitation_code = f"DECLINECODE_{uuid.uuid4().hex[:8].upper()}"
        with self.class_under_test(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        ) as manager:
            invitation_data = {
                "code": invitation_code,
                "team_id": test_team.id,
                "role_id": env("USER_ROLE_ID"),
                "user_id": test_admin.id,
                "max_uses": 5,
            }
            invitation = manager.create(**invitation_data)

            # Create a user to decline the invitation
            user_data = {
                "email": f"unified_decliner_{uuid.uuid4().hex[:8]}@example.com",
                "username": f"unified_decliner_{uuid.uuid4().hex[:8]}",
                "display_name": "Unified Decliner",
                "password": "TestPassword123!",
            }
            user = UserManager.register(user_data, self.model_registry)

            # Test unified decline via code
            from logic.BLL_Auth import InvitationModel

            decline_data = InvitationModel.Patch(
                invitation_code=invitation_code, action="decline"
            )
            result = manager.patch_invitation_unified(decline_data, user.id)

            assert result["success"] is True
            assert result["message"] == "Invitation declined successfully via code"

    def test_unified_invitation_decline_via_invitee_id(
        self, admin_a, team_a, server, model_registry
    ):
        """Test the unified invitation decline method via invitee ID."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import TeamManager, UserManager

        # Create test entities
        test_admin_data = {
            "email": f"test_unified_decline_invitee_admin_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_unified_decline_invitee_admin_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Unified Decline Invitee Admin",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)
        test_admin_id = test_admin.id

        with TeamManager(
            requester_id=test_admin_id,
            model_registry=self.model_registry,
        ) as team_manager:
            test_team = team_manager.create(
                name="Test Unified Decline Invitee Team",
                description="Team for unified decline invitee invitation testing",
            )
            test_team_id = test_team.id

        # Create a team-based invitation (will have a code auto-generated)
        with self.class_under_test(
            requester_id=test_admin_id,
            target_team_id=test_team_id,
            model_registry=self.model_registry,
        ) as invitation_manager:
            invitation_data = {
                "team_id": test_team_id,
                "role_id": env("USER_ROLE_ID"),
                "user_id": test_admin_id,
            }
            invitation = invitation_manager.create(**invitation_data)

            # Add specific invitee
            invitee_email = (
                f"unified_decliner_invitee_{uuid.uuid4().hex[:8]}@example.com"
            )
            invitee_result = invitation_manager.add_invitee(
                invitation.id, invitee_email
            )

            # Team-based invitations have codes and links
            assert invitee_result["invitation_link"] is not None
            assert invitee_result["invitation_code"] is not None
            assert invitation.code in invitee_result["invitation_link"]

        # Create user with matching email
        user_data = {
            "email": invitee_email,
            "username": f"unified_decliner_invitee_{uuid.uuid4().hex[:8]}",
            "display_name": "Unified Decliner Invitee",
            "password": "TestPassword123!",
        }
        user = UserManager.register(user_data, self.model_registry)
        user_id = user.id

        # Get the invitee ID
        with InviteeManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        ) as invitee_manager:
            invitees = invitee_manager.list(email=invitee_email)
            assert len(invitees) == 1
            invitee_id = invitees[0].id

        # Test unified acceptance via invitee ID using a fresh manager
        with self.class_under_test(
            requester_id=env("ROOT_ID"),
            target_team_id=test_team_id,
            model_registry=self.model_registry,
        ) as fresh_invitation_manager:
            from logic.BLL_Auth import InvitationModel

            patch_data = InvitationModel.Patch(invitee_id=invitee_id, action="decline")
            result = fresh_invitation_manager.patch_invitation_unified(
                patch_data, user_id
            )

            assert result["success"] is True
            assert (
                result["message"] == "Invitation declined successfully via invitee ID"
            )
            assert result["team_id"] == test_team_id
            assert result["role_id"] == env("USER_ROLE_ID")

    def test_unified_acceptance_validation(
        self, admin_a, team_a, server, model_registry
    ):
        """Test validation of unified acceptance methods."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import InvitationModel, UserManager

        # Test validation scenarios
        test_code = f"TESTCODE_{uuid.uuid4().hex[:8].upper()}"

        with pytest.raises(
            HTTPException,
            match="Exactly one of invitation_code or invitee_id must be provided",
        ):
            # Both methods provided
            InvitationModel.Patch(
                invitation_code=test_code, invitee_id="test-invitee-id"
            )

        # Valid cases should not raise errors
        accept_via_code = InvitationModel.Patch(invitation_code=test_code)
        assert accept_via_code.invitation_code == test_code
        assert accept_via_code.invitee_id is None

        accept_via_invitee = InvitationModel.Patch(invitee_id="test-invitee-id")
        assert accept_via_invitee.invitee_id == "test-invitee-id"
        assert accept_via_invitee.invitation_code is None

    def test_invalid_invitation_acceptance_scenarios(
        self, admin_a, team_a, server, model_registry
    ):
        """Test various invalid scenarios for invitation acceptance."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import InvitationModel, UserManager

        # Test 1: Invalid invitation code should raise HTTPException
        test_user_data = {
            "email": f"test_invalid_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_invalid_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Invalid User",
            "password": "TestPassword123!",
        }
        test_user = UserManager.register(test_user_data, self.model_registry)
        test_user_id = test_user.id

        manager = self.class_under_test(
            requester_id=test_user_id, model_registry=self.model_registry
        )
        invalid_code = f"INVALIDCODE_{uuid.uuid4().hex[:8].upper()}"
        accept_data = InvitationModel.Accept(invitation_code=invalid_code)
        with pytest.raises(HTTPException) as exc_info:
            manager.accept_invitation_unified(accept_data, test_user_id)
        assert exc_info.value.status_code == 404

        # Test 2: Invalid invitee ID should raise HTTPException - use fresh manager
        fresh_manager = self.class_under_test(
            requester_id=test_user_id, model_registry=self.model_registry
        )
        accept_data = InvitationModel.Accept(invitee_id="invalid-invitee-id")
        with pytest.raises(HTTPException) as exc_info:
            fresh_manager.accept_invitation_unified(accept_data, test_user_id)
        assert exc_info.value.status_code == 404


class TestInviteeManager(AbstractBLLTest):
    """
    Tests for the InviteeManager.

    Note: These tests focus on the low-level invitation acceptance methods.
    For unified invitation acceptance (the main API), see TestInvitationManager.test_unified_* tests.
    """

    class_under_test = InviteeManager
    create_fields = {
        "email": f"test_{faker.word()}_{faker.random_int()}@example.com",
    }
    update_fields = {
        "accepted_at": datetime.now(),
    }
    parent_entities = [
        ParentEntity(
            name="invitation",
            foreign_key="invitation_id",
            test_class=TestInvitationManager,
        ),
    ]

    def _create(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        key="create",
        server=None,
        model_registry=None,
    ):
        """Override to create invitation parent entity manually."""
        # Get model_registry from server if not provided
        if model_registry is None and server is not None:
            model_registry = server.app.state.model_registry
        elif model_registry is None and hasattr(self, "model_registry"):
            model_registry = self.model_registry

        from logic.BLL_Auth import InvitationManager, TeamManager, UserManager

        # Create a simple invitation manually to avoid complex parent entity dependencies
        test_user_data = {
            "email": f"test_invitee_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_invitee_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Invitee User",
            "password": "TestPassword123!",
        }
        test_user = UserManager.register(test_user_data, model_registry)

        team_manager = TeamManager(
            requester_id=test_user.id, model_registry=model_registry
        )
        test_team = team_manager.create(
            name=f"Test Invitee Team {uuid.uuid4().hex[:8]}",
            description="Team for invitation invitee testing",
        )

        # Create invitation
        invitation_manager = InvitationManager(
            requester_id=test_user.id,
            target_team_id=test_team.id,
            model_registry=model_registry,
        )
        invitation_data = {
            "code": f"TESTINVITEE_{uuid.uuid4().hex[:8].upper()}",
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_user.id,
            "max_uses": 5,
        }
        invitation = invitation_manager.create(**invitation_data)

        # Now create the invitation invitee
        manager = self.class_under_test(
            requester_id=user_id, model_registry=model_registry
        )
        create_data = self.create_fields.copy()
        for field in create_data:
            if callable(create_data[field]):
                create_data[field] = create_data[field]()

        # Set the invitation_id from our manually created invitation
        create_data["invitation_id"] = invitation.id

        self.tracked_entities[key] = manager.create(**create_data)
        return self.tracked_entities[key]

    def _list(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        model_registry=None,
    ):
        """Override to filter out invalid records with None invitation_id."""
        # Get model_registry if not provided
        if model_registry is None and hasattr(self, "model_registry"):
            model_registry = self.model_registry

        manager = self.class_under_test(
            requester_id=user_id, model_registry=model_registry
        )
        # Use filters to exclude records with None invitation_id
        from logic.BLL_Auth import InviteeModel

        filters = [InviteeModel.invitation_id != None]
        self.tracked_entities["list_result"] = manager.list(filters=filters)

    def test_accept_invitation_code_valid(
        self, admin_a, team_a, server, model_registry
    ):
        """Test accepting a valid invitation code."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import InvitationManager, TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_admin_valid_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_admin_valid_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Admin Valid",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_admin.id, model_registry=self.model_registry
        )
        test_team = team_manager.create(
            name="Test Valid Team",
            description="Team for valid invitation testing",
        )

        # Create an invitation first
        valid_code = f"VALIDCODE_{uuid.uuid4().hex[:8].upper()}"
        invitation_manager = InvitationManager(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": valid_code,
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin.id,
            "max_uses": 5,
        }
        invitation = invitation_manager.create(**invitation_data)

        # Create a user to accept the invitation
        user_data = {
            "email": f"accepter_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"accepter_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Accepter",
            "password": "TestPassword123!",
        }
        user = UserManager.register(user_data, self.model_registry)

        # Accept the invitation
        manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        result = manager.accept_invitation(valid_code, user.id)

        assert result["success"] is True
        assert result["team_id"] == test_team.id
        assert result["role_id"] == env("USER_ROLE_ID")
        assert "user_team_id" in result

    def test_accept_invitation_code_invalid_expired(
        self, admin_a, team_a, server, model_registry
    ):
        """Test rejecting when invitation expired."""
        self.server = server
        self.model_registry = model_registry
        from datetime import datetime, timedelta, timezone

        from logic.BLL_Auth import InvitationManager, TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_admin_expired_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_admin_expired_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Admin Expired",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)

        team_manager = TeamManager(
            requester_id=test_admin.id, model_registry=self.model_registry
        )
        test_team = team_manager.create(
            name="Test Expired Team",
            description="Team for expired invitation testing",
        )

        # Create an expired invitation
        expired_code = f"EXPIREDCODE_{uuid.uuid4().hex[:8].upper()}"
        expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
        invitation_manager = InvitationManager(
            requester_id=test_admin.id,
            target_team_id=test_team.id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": expired_code,
            "team_id": test_team.id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin.id,
            "expires_at": expired_time,
        }
        invitation = invitation_manager.create(**invitation_data)

        # Create a user to attempt acceptance
        user_data = {
            "email": f"expired_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"expired_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Expired User",
            "password": "TestPassword123!",
        }
        user = UserManager.register(user_data, self.model_registry)

        # Attempt to accept expired invitation
        manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        with pytest.raises(HTTPException) as exc_info:
            manager.accept_invitation(expired_code, user.id)

        assert exc_info.value.status_code == 410
        assert "expired" in exc_info.value.detail

    def test_accept_invitation_code_invalid_too_many(
        self, admin_a, team_a, server, model_registry
    ):
        """Test rejecting invitation when max uses exceeded."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import InvitationManager, TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_admin_maxuse_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_admin_maxuse_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Admin MaxUse",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)
        test_admin_id = test_admin.id

        team_manager = TeamManager(
            requester_id=test_admin_id,
            model_registry=self.model_registry,
        )
        test_team = team_manager.create(
            name="Test MaxUse Team",
            description="Team for max use invitation testing",
        )
        test_team_id = test_team.id

        # Create an invitation with max_uses = 1
        max_use_code = f"MAXUSECODE_{uuid.uuid4().hex[:8].upper()}"
        invitation_manager = InvitationManager(
            requester_id=test_admin_id,
            target_team_id=test_team_id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": max_use_code,
            "team_id": test_team_id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin_id,
            "max_uses": 1,
        }
        invitation = invitation_manager.create(**invitation_data)

        # Create two users
        user_data1 = {
            "email": f"maxuse_user1_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"maxuse_user1_{uuid.uuid4().hex[:8]}",
            "display_name": "MaxUse User 1",
            "password": "TestPassword123!",
        }
        user1 = UserManager.register(user_data1, self.model_registry)
        user1_id = user1.id

        user_data2 = {
            "email": f"maxuse_user2_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"maxuse_user2_{uuid.uuid4().hex[:8]}",
            "display_name": "MaxUse User 2",
            "password": "TestPassword123!",
        }
        user2 = UserManager.register(user_data2, self.model_registry)
        user2_id = user2.id

        # First user accepts successfully
        invitee_manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        result1 = invitee_manager.accept_invitation(max_use_code, user1_id)
        assert result1["success"] is True

        # Second user should fail (max uses exceeded)
        invitee_manager2 = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        with pytest.raises(HTTPException) as exc_info:
            invitee_manager2.accept_invitation(max_use_code, user2_id)

    def test_accept_invitation_user(self, admin_a, team_a, server, model_registry):
        """Test user accepting an invitation they were specifically invited to."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import InvitationManager, TeamManager, UserManager

        # Create our own test entities using context managers to ensure proper session handling
        test_admin_data = {
            "email": f"test_admin_specific_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_admin_specific_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Admin Specific",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)
        test_admin_id = test_admin.id

        team_manager = TeamManager(
            requester_id=test_admin_id, model_registry=self.model_registry
        )
        test_team = team_manager.create(
            name="Test Specific Team",
            description="Team for specific invitation testing",
        )
        test_team_id = test_team.id

        # Create an invitation
        user_code = f"USERCODE_{uuid.uuid4().hex[:8].upper()}"
        invitation_manager = InvitationManager(
            requester_id=test_admin_id,
            target_team_id=test_team_id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            "code": user_code,
            "team_id": test_team_id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin_id,
        }
        invitation = invitation_manager.create(**invitation_data)

        # Add specific invitee
        invitee_email = f"specific_user_{uuid.uuid4().hex[:8]}@example.com"
        invitation_manager.add_invitee(invitation.id, invitee_email)

        # Store invitation ID to avoid detached session issues
        invitation_id = invitation.id

        # Create user with matching email
        user_data = {
            "email": invitee_email,
            "username": f"specific_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Specific User",
            "password": "TestPassword123!",
        }
        user = UserManager.register(user_data, self.model_registry)
        user_id = user.id

        # Accept the invitation using a fresh manager
        invitee_manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        result = invitee_manager.accept_invitation(user_code, user_id)

        assert result["success"] is True
        assert result["team_id"] == test_team_id
        assert result["role_id"] == env("USER_ROLE_ID")

        # Create a fresh manager for verification to avoid detached session issues
        verify_manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        # Check for accepted invitations using accepted_at field instead of is_accepted
        invitees = verify_manager.list(email=invitee_email)
        accepted_invitees = [inv for inv in invitees if inv.accepted_at is not None]
        assert len(accepted_invitees) == 1
        assert accepted_invitees[0].user_id == user_id

    def test_decline_invitation_user(self, admin_a, team_a, server, model_registry):
        """Test user declining a direct invitation (no code involved)."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import InvitationManager, TeamManager, UserManager

        # Create our own test entities
        test_admin_data = {
            "email": f"test_admin_decline_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_admin_decline_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Admin Decline",
            "password": "TestPassword123!",
        }
        test_admin = UserManager.register(test_admin_data, self.model_registry)
        test_admin_id = test_admin.id

        team_manager = TeamManager(
            requester_id=test_admin_id, model_registry=self.model_registry
        )
        test_team = team_manager.create(
            name="Test Decline Team",
            description="Team for decline invitation testing",
        )
        test_team_id = test_team.id

        # Create a direct invitation (no code - these are the ones that can be declined)
        invitation_manager = InvitationManager(
            requester_id=test_admin_id,
            target_team_id=test_team_id,
            model_registry=self.model_registry,
        )
        invitation_data = {
            # No code for direct invitations
            "team_id": test_team_id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": test_admin_id,
        }
        invitation = invitation_manager.create(**invitation_data)

        # Add specific invitee using the add_invitee method
        invitee_email = f"decline_user_{uuid.uuid4().hex[:8]}@example.com"
        invitation_manager.add_invitee(invitation.id, invitee_email)

        # Create user but don't accept invitation
        user_data = {
            "email": invitee_email,
            "username": f"decline_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Decline User",
            "password": "TestPassword123!",
        }
        user = UserManager.register(user_data, self.model_registry)
        user_id = user.id

        # Store ID to avoid detached session issues
        root_id = env("ROOT_ID")

        # Verify the invitation remains unaccepted (no accepted_at timestamp)
        invitee_manager = self.class_under_test(
            requester_id=root_id, model_registry=self.model_registry
        )
        invitees = invitee_manager.list(email=invitee_email)
        assert len(invitees) == 1
        assert invitees[0].accepted_at is None
        assert invitees[0].declined_at is None

        # Simulate explicit decline by setting declined_at timestamp and user_id
        from datetime import datetime

        invitee_manager.update(
            id=invitees[0].id, declined_at=datetime.now(), user_id=user_id
        )

        # Create a fresh manager for final verification
        verify_manager = self.class_under_test(
            requester_id=root_id, model_registry=self.model_registry
        )
        updated_invitees = verify_manager.list(email=invitee_email)
        assert len(updated_invitees) == 1
        assert updated_invitees[0].accepted_at is None  # Still not accepted
        assert updated_invitees[0].declined_at is not None  # Now declined
        assert updated_invitees[0].user_id == user_id


class TestUserCredentialManager(AbstractBLLTest):
    class_under_test = UserCredentialManager
    create_fields = {
        "user_id": None,  # Will be set in tests
        "password": "TestPassword123!",
    }
    update_fields = {
        "password_changed_at": datetime.now(),
    }
    parent_entities = [
        ParentEntity(name="user", foreign_key="user_id", test_class=TestUserManager),
    ]

    def test_change_password(self, admin_a, team_a, server, model_registry):
        """Test changing a user's password and verify changed_at is updated correctly."""
        self.server = server
        self.model_registry = model_registry
        from logic.BLL_Auth import (
            UserCredentialManager,
            UserCredentialModel,
            UserManager,
        )

        # Get the SQLAlchemy model from the BLL model
        UserCredential = UserCredentialModel.DB(self.model_registry.DB.Base)

        # Create a test user
        test_user_data = {
            "email": f"test_pwd_user_{uuid.uuid4().hex[:8]}@example.com",
            "username": f"test_pwd_user_{uuid.uuid4().hex[:8]}",
            "display_name": "Test Password User",
            "password": "InitialPassword123!",
        }
        test_user = UserManager.register(test_user_data, self.model_registry)
        user_id = test_user.id

        # Get the current credential
        with UserCredentialManager(
            requester_id=user_id, model_registry=self.model_registry
        ) as credential_manager:
            # Find current active credential
            credentials = UserCredential.list(
                requester_id=user_id,
                model_registry=self.model_registry,
                user_id=user_id,
                filters=[
                    UserCredential.password_changed_at == None,
                    UserCredential.deleted_at == None,
                ],
            )

            assert len(credentials) == 1, "Should have one active credential"
            initial_credential_id = (
                credentials[0]["id"]
                if isinstance(credentials[0], dict)
                else credentials[0].id
            )

            # Change the password
            result = credential_manager.change_password(
                user_id=user_id,
                current_password="InitialPassword123!",
                new_password="NewPassword456!",
            )

            assert result["message"] == "Password changed successfully"

        # Verify the old credential now has password_changed_at set using a fresh manager
        with UserCredentialManager(
            requester_id=user_id, model_registry=self.model_registry
        ) as verify_manager:
            updated_credential = UserCredential.get(
                requester_id=user_id,
                model_registry=self.model_registry,
                id=initial_credential_id,
            )

            old_pwd_changed = (
                updated_credential["password_changed_at"]
                if isinstance(updated_credential, dict)
                else updated_credential.password_changed_at
            )
            assert (
                old_pwd_changed is not None
            ), "Old credential should have password_changed_at set"

            # Verify there's a new current credential
            new_credentials = UserCredential.list(
                requester_id=user_id,
                model_registry=self.model_registry,
                user_id=user_id,
                filters=[
                    UserCredential.password_changed_at == None,
                    UserCredential.deleted_at == None,
                ],
            )

            assert (
                len(new_credentials) == 1
            ), "User should have exactly one new current credential"
            new_cred_id = (
                new_credentials[0]["id"]
                if isinstance(new_credentials[0], dict)
                else new_credentials[0].id
            )
            assert (
                new_cred_id != initial_credential_id
            ), "New credential should have a different ID"

        # Use a fresh UserCredentialManager with root user to change password
        with UserCredentialManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        ) as root_credential_manager:
            result = root_credential_manager.change_password(
                user_id=user_id,
                current_password="NewPassword456!",
                new_password="RootPasswordChange456!",
            )

            assert result["message"] == "Password changed successfully"

        # Verify there's a new current credential created by root user
        with UserCredentialManager(
            requester_id=user_id,
            model_registry=self.model_registry,
        ) as final_verify_manager:
            root_changed_credentials = UserCredential.list(
                requester_id=user_id,
                model_registry=self.model_registry,
                user_id=user_id,
                filters=[
                    UserCredential.password_changed_at == None,
                    UserCredential.deleted_at == None,
                ],
            )

            assert (
                len(root_changed_credentials) == 1
            ), "User should have exactly one new current credential"
            root_cred_created_by = (
                root_changed_credentials[0]["created_by_user_id"]
                if isinstance(root_changed_credentials[0], dict)
                else root_changed_credentials[0].created_by_user_id
            )
            assert root_cred_created_by == env(
                "ROOT_ID"
            ), "New credential should be created by root user"

            # Change the password again to verify the fix works for subsequent changes
            second_credential_id = (
                root_changed_credentials[0]["id"]
                if isinstance(root_changed_credentials[0], dict)
                else root_changed_credentials[0].id
            )

        # Create a fresh credential manager for the third password change
        with UserCredentialManager(
            requester_id=user_id, model_registry=self.model_registry
        ) as fresh_credential_manager:
            result = fresh_credential_manager.change_password(
                user_id=user_id,
                current_password="RootPasswordChange456!",
                new_password="ThirdPassword789!",
            )

            assert result["message"] == "Password changed successfully"

        # Final verification
        with UserCredentialManager(
            requester_id=user_id,
            model_registry=self.model_registry,
        ) as final_manager:
            # Verify the second credential is marked as changed
            second_credential = UserCredential.get(
                requester_id=user_id,
                model_registry=self.model_registry,
                id=second_credential_id,
            )
            second_pwd_changed = (
                second_credential["password_changed_at"]
                if isinstance(second_credential, dict)
                else second_credential.password_changed_at
            )
            assert (
                second_pwd_changed is not None
            ), "Second credential should be marked as changed"

            # Verify there's a third active credential
            third_credentials = UserCredential.list(
                requester_id=user_id,
                model_registry=self.model_registry,
                user_id=user_id,
                filters=[
                    UserCredential.password_changed_at == None,
                    UserCredential.deleted_at == None,
                ],
            )

            assert (
                len(third_credentials) == 1
            ), "Should have one third active credential"
            third_cred_id = (
                third_credentials[0]["id"]
                if isinstance(third_credentials[0], dict)
                else third_credentials[0].id
            )
            assert (
                third_cred_id != second_credential_id
            ), "Third credential should have different ID"
            third_cred_created_by = (
                third_credentials[0]["created_by_user_id"]
                if isinstance(third_credentials[0], dict)
                else third_credentials[0].created_by_user_id
            )
            assert (
                third_cred_created_by == user_id
            ), "Third credential should be created by the user"


class TestPermissionManager(AbstractBLLTest):
    class_under_test = PermissionManager
    create_fields = {
        "resource_type": "invitations",
        "resource_id": None,  # Will be set by parent entity
        "user_id": None,  # Will be set for user permissions
        "team_id": None,  # Will be set for team permissions
        "role_id": None,  # Will be set for role permissions
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
            name="invitation",
            foreign_key="resource_id",
            test_class=TestInvitationManager,
        ),
    ]

    def _create(
        self,
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        key="create",
        server=None,
        model_registry=None,
    ):
        """Override to ensure permissions are created with proper user_id."""
        # Get model_registry from server if not provided
        if model_registry is None and server is not None:
            model_registry = server.app.state.model_registry
        elif model_registry is None and hasattr(self, "model_registry"):
            model_registry = self.model_registry

        # Get server if not provided
        if server is None and hasattr(self, "server"):
            server = self.server

        manager = self.class_under_test(
            requester_id=user_id,
            target_team_id=team_id,
            model_registry=model_registry,
        )
        # For base tests, we'll create user permissions by default
        create_data = {}
        if key in ["create_team_perm", "create_team_role_perm", "create_role_perm"]:
            # For team and role permissions, don't use build_entities to avoid user_id
            create_data = self.create_fields.copy()
        else:
            # For user permissions, use build_entities to get proper user_id
            create_data = self.build_entities(
                server,
                user_id=user_id,
                team_id=team_id,
            )[0]
            create_data["user_id"] = user_id
            # Remove team_id and role_id to avoid validation error
            create_data.pop("team_id", None)
            create_data.pop("role_id", None)

        self.tracked_entities[key] = manager.create(**create_data)
        return self.tracked_entities[key]

    def _create_user_permission(self, user_id: str, resource_id: str, **kwargs):
        """Helper method to create a user permission (Case 1)."""
        create_data = self.create_fields.copy()
        create_data.update({"resource_id": resource_id, "user_id": user_id, **kwargs})
        # Ensure team_id and role_id are not present
        create_data.pop("team_id", None)
        create_data.pop("role_id", None)
        # Create as the target user so the row is self-owned. The strict
        # permission filter hides ROOT-created records from non-ROOT viewers,
        # which would otherwise prevent the target user from listing their own
        # permission grants.
        manager = self.class_under_test(
            requester_id=user_id, model_registry=self.model_registry
        )
        return manager.create(**create_data)

    def _create_team_permission(self, team_id: str, resource_id: str, **kwargs):
        """Helper method to create a team permission (Case 2)."""
        # Start with base fields
        create_data = {
            "resource_type": "invitations",
            "resource_id": resource_id,
            "team_id": team_id,
            "user_id": None,  # Explicitly set to None
            "role_id": None,  # Explicitly set to None
            "can_view": True,
            "can_edit": False,
            "can_delete": False,
            "can_share": False,
        }
        # Update with any overrides
        create_data.update(kwargs)

        # Create directly without using base _create
        manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        permission = manager.create(**create_data)
        # Store in tracked entities for assertions
        self.tracked_entities["create_team_perm"] = permission
        return permission

    def _create_team_role_permission(self, team_id: str, resource_id: str, **kwargs):
        """Helper method to create a team+role permission (Case 3)."""
        # Start with base fields
        create_data = {
            "resource_type": "invitations",
            "resource_id": resource_id,
            "team_id": team_id,
            "role_id": env("USER_ROLE_ID"),
            "user_id": None,  # Explicitly set to None
            "can_view": True,
            "can_edit": False,
            "can_delete": False,
            "can_share": False,
        }
        # Update with any overrides
        create_data.update(kwargs)

        # Create directly without using base _create
        manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        permission = manager.create(**create_data)
        # Store in tracked entities for assertions
        self.tracked_entities["create_team_role_perm"] = permission
        return permission

    def _create_role_permission(
        self, role_id: str, resource_id: str, team_id: str = None, **kwargs
    ):
        """Helper method to create a role-specific permission (Case 4)."""
        # For team-specific roles, we need to include the team_id
        # Since role access validation is complex, accept team_id as parameter

        # Start with base fields
        create_data = {
            "resource_type": "invitations",
            "resource_id": resource_id,
            "role_id": role_id,
            "user_id": None,  # Explicitly set to None
            "team_id": team_id,  # Include team_id for team-specific roles
            "can_view": True,
            "can_edit": False,
            "can_delete": False,
            "can_share": False,
        }
        # Update with any overrides
        create_data.update(kwargs)

        # Create directly without using base _create - use ROOT_ID for permissions
        manager = self.class_under_test(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        permission = manager.create(**create_data)
        # Store in tracked entities for assertions
        self.tracked_entities["create_role_perm"] = permission
        return permission

    def test_create(self, admin_a, team_a, server, model_registry):
        """Override: Test creating a basic permission."""
        self.server = server
        self.model_registry = model_registry
        # Create a user permission for basic create test
        resource_id = faker.uuid4()
        permission = self._create_user_permission(admin_a.id, resource_id)
        self.tracked_entities["create"] = permission
        self._create_assert("create")

    def test_create_user_permission(self, admin_a, team_a, server, model_registry):
        """Test creating a user-specific permission (Case 1)."""
        self.server = server
        self.model_registry = model_registry
        resource_id = faker.uuid4()
        permission = self._create_user_permission(
            admin_a.id,
            resource_id,
            can_view=True,
            can_edit=True,
        )
        self.tracked_entities["create_user_perm"] = permission
        self._create_assert("create_user_perm")

        assert permission.user_id == admin_a.id
        assert permission.resource_id == resource_id
        assert permission.can_view is True
        assert permission.can_edit is True
        assert permission.team_id is None
        assert permission.role_id is None

    def test_create_team_permission(self, admin_a, team_a, server, model_registry):
        """Test creating a team-specific permission (Case 2)."""
        self.server = server
        self.model_registry = model_registry
        resource_id = faker.uuid4()
        permission = self._create_team_permission(
            team_a.id,
            resource_id,
            can_view=True,
            can_share=True,
        )
        self.tracked_entities["create_team_perm"] = permission
        self._create_assert("create_team_perm")

        assert permission.team_id == team_a.id
        assert permission.resource_id == resource_id
        assert permission.can_view is True
        assert permission.can_share is True
        assert permission.user_id is None
        assert permission.role_id is None

    def test_create_team_role_permission(self, admin_a, team_a, server, model_registry):
        """Test creating a team+role permission (Case 3)."""
        self.server = server
        self.model_registry = model_registry
        resource_id = faker.uuid4()
        permission = self._create_team_role_permission(
            team_a.id,
            resource_id,
            can_view=True,
            can_execute=True,
        )
        self.tracked_entities["create_team_role_perm"] = permission
        self._create_assert("create_team_role_perm")

        assert permission.team_id == team_a.id
        assert permission.role_id == env("USER_ROLE_ID")
        assert permission.resource_id == resource_id
        assert permission.can_view is True
        assert permission.can_execute is True
        assert permission.user_id is None

    def test_create_role_permission(self, admin_a, team_a, server, model_registry):
        """Test creating a role-specific permission (Case 4)."""
        self.server = server
        self.model_registry = model_registry
        resource_id = faker.uuid4()

        # First create a team-specific role
        with RoleManager(
            requester_id=admin_a.id, model_registry=self.model_registry
        ) as role_manager:
            team_role = role_manager.create(
                name=f"Test Role {faker.word()}",
                team_id=team_a.id,
                mfa_count=1,
            )

        permission = self._create_role_permission(
            team_role.id,
            resource_id,
            team_id=team_a.id,  # Pass team_id explicitly
            can_view=True,
            can_execute=True,
        )
        self.tracked_entities["create_role_perm"] = permission
        self._create_assert("create_role_perm")

        assert permission.role_id == team_role.id
        assert permission.resource_id == resource_id
        assert permission.can_view is True
        assert permission.can_execute is True
        assert permission.user_id is None
        assert permission.team_id == team_a.id  # Team-specific role requires team_id

    # @pytest.mark.dependency(depends=["test_create_user_permission"])
    def test_list_permissions(self, admin_a, team_a, server, model_registry):
        """Test listing permissions with filters."""
        self.server = server
        self.model_registry = model_registry
        resource_id_1 = faker.uuid4()
        resource_id_2 = faker.uuid4()
        resource_id_3 = faker.uuid4()

        # Create multiple permissions
        perm1 = self._create_user_permission(admin_a.id, resource_id_1, can_view=True)
        self.tracked_entities["list_1"] = perm1

        perm2 = self._create_user_permission(admin_a.id, resource_id_2, can_edit=True)
        self.tracked_entities["list_2"] = perm2

        perm3 = self._create_user_permission(admin_a.id, resource_id_3, can_edit=True)
        self.tracked_entities["list_3"] = perm3

        # List permissions
        self._list(admin_a.id, team_a.id)
        self._list_assert("list_result")

        # Filter by resource_type and specific resource IDs
        manager = self.class_under_test(
            requester_id=admin_a.id, model_registry=self.model_registry
        )
        filtered = manager.search(
            resource_type="invitations",
            user_id=admin_a.id,
            resource_id=resource_id_1,
        )
        assert len(filtered) == 1

        filtered = manager.search(
            resource_type="invitations",
            user_id=admin_a.id,
            resource_id=resource_id_2,
        )
        assert len(filtered) == 1

    # @pytest.mark.dependency(depends=["test_create_user_permission"])
    def test_permission_validation(self, admin_a, team_a, server, model_registry):
        """Test permission creation validation."""
        self.server = server
        self.model_registry = model_registry
        resource_id = faker.uuid4()

        # Test creating permission without user_id, team_id, or role_id
        with pytest.raises(ValueError, match="Invalid permission combination"):
            create_data = {
                "resource_type": "invitations",
                "resource_id": resource_id,
                "can_view": True,
                "can_edit": False,
                "can_delete": False,
                "can_share": False,
                "user_id": None,
                "team_id": None,
                "role_id": None,
            }
            # Test Pydantic validation directly
            self.class_under_test.Model.Create(**create_data)

        # Test creating permission with invalid user_id
        with pytest.raises(HTTPException) as exc_info:
            self._create_user_permission(faker.uuid4(), resource_id, can_view=True)
        assert exc_info.value.status_code == 404

        # Test creating permission with both user_id and team_id
        with pytest.raises(ValueError, match="Invalid permission combination"):
            create_data = {
                "resource_type": "invitations",
                "resource_id": resource_id,
                "user_id": admin_a.id,
                "team_id": team_a.id,
                "can_view": True,
                "can_edit": False,
                "can_delete": False,
                "can_share": False,
                "role_id": None,
            }
            # Test Pydantic validation directly
            self.class_under_test.Model.Create(**create_data)

        # First create a team-specific role
        with RoleManager(
            requester_id=admin_a.id, model_registry=self.model_registry
        ) as role_manager:
            team_role = role_manager.create(
                name=f"Test Role {faker.word()}",
                team_id=team_a.id,
                mfa_count=1,
            )

        # Test creating permission with both user_id and role_id (without team_id)
        with pytest.raises(ValueError, match="Cannot have both user_id and role_id"):
            create_data = {
                "resource_type": "invitations",
                "resource_id": resource_id,
                "user_id": admin_a.id,
                "role_id": team_role.id,  # Use team-specific role
                "can_view": True,
                "can_edit": False,
                "can_delete": False,
                "can_share": False,
                "team_id": None,  # This triggers the validation error
            }
            # Test Pydantic validation directly
            self.class_under_test.Model.Create(**create_data)

        # Test creating permission with non-team-specific role without team_id
        with pytest.raises(
            ValueError, match="Role must be team-specific when used without team_id"
        ):
            create_data = {
                "resource_type": "invitations",
                "resource_id": resource_id,
                "user_id": None,
                "team_id": None,
                "role_id": env("USER_ROLE_ID"),  # System role (not team-specific)
                "can_view": True,
                "can_edit": False,
                "can_delete": False,
                "can_share": False,
            }
            # Test Pydantic validation directly
            self.class_under_test.Model.Create(**create_data)

    def test_search(
        self, admin_a, team_a, server, model_registry, search_field, search_operator
    ):
        """Override: Test search functionality for permissions using proper _create method."""
        self.server = server
        self.model_registry = model_registry

        # Create entity if not already created using our custom _create method

        if not hasattr(self, "_search_test_entity"):
            # Use our custom _create method which handles permission validation properly
            self._search_test_entity = self._create(
                admin_a.id,
                team_a.id,
                key="search_test",
                server=server,
                model_registry=model_registry,
            )

        # Get the entity for search testing
        entity = self._search_test_entity

        # Convert entity to dict
        entity_dict = (
            entity.model_dump() if hasattr(entity, "model_dump") else entity.__dict__
        )

        # Get field value
        field_value = entity_dict.get(search_field)
        if field_value is None:
            pytest.skip(f"Field {search_field} is None in test entity")

        # Get search value for operator
        search_value = self.get_search_value_for_operator(field_value, search_operator)

        # Construct search criteria
        if search_operator == "value":
            # Direct value syntax (implicit eq)
            search_criteria = {search_field: search_value}
        else:
            # Nested operator syntax
            search_criteria = {search_field: {search_operator: search_value}}

        # Perform search
        requester_id = env("SYSTEM_ID") if self.is_system_entity else admin_a.id
        manager = self.class_under_test(
            requester_id=requester_id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )
        results = manager.search(**search_criteria)

        # Verify entity is found
        result_ids = [r.id for r in results]
        assert entity.id in result_ids, (
            f"Entity not found when searching {search_field} with operator '{search_operator}' "
            f"and value '{search_value}' (type: {type(search_value).__name__}). Found {len(results)} results."
        )
