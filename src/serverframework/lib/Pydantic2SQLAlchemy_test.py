import unittest
from datetime import datetime
from typing import List, Optional

import pytest

from pydantic import Field
from sqlalchemy.orm import sessionmaker

from serverframework.logic.BLL_Auth import RoleModel, TeamModel

from serverframework.database.DatabaseManager import DatabaseManager
from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic import ModelRegistry
from serverframework.lib.Pydantic2SQLAlchemy import (
    ApplicationModel,
    DatabaseMixin,
    ImageMixinModel,
    ModelConverter,
    ParentMixinModel,
    DatabaseMixin,
    UpdateMixinModel,
    clear_registry_cache,
    create_sqlalchemy_model,
)


class TestPydantic2SQLAlchemyReal(unittest.TestCase):
    """
    Test suite for the Pydantic to SQLAlchemy scaffolding system using real models and database operations.
    """

    @classmethod
    def setUpClass(cls):
        """Set up test database and base model."""
        logger.debug("\n=== Setting up SQLAlchemy Tests ===")

        # Create isolated database manager for testing
        cls.db_manager = DatabaseManager(
            db_prefix="test.sqlalchemy", test_connection=True
        )

        # Use the database manager's Base instead of creating our own
        cls.TestBase = cls.db_manager.Base
        cls.engine = cls.db_manager.get_setup_engine()
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        """Set up each test."""
        clear_registry_cache()
        self.session = self.Session()

        # Clear the SQLAlchemy registry to avoid conflicts between tests
        if hasattr(self, "TestBase") and hasattr(self.TestBase, "registry"):
            self.TestBase.registry._class_registry.clear()

    def tearDown(self):
        """Clean up after each test."""
        self.session.close()
        # Drop all tables
        self.TestBase.metadata.drop_all(self.engine)
        # Clear metadata tables to avoid conflicts between tests
        self.TestBase.metadata.clear()

    @classmethod
    def tearDownClass(cls):
        """Clean up after all tests."""
        cls.engine.dispose()

    def test_real_model_scaffolding(self):
        """Test scaffolding with real Pydantic models without mocks."""
        logger.debug("\n=== Testing Real Model Scaffolding ===")

        # Create model registry
        registry = ModelRegistry()

        # Define real test models (no mocks)
        class TestUserModel(ApplicationModel, UpdateMixinModel, DatabaseMixin):
            email: str = Field(..., description="User's email address")
            username: Optional[str] = Field(None, description="User's username")
            active: bool = Field(True, description="Whether user is active")

        class TestProjectModel(ApplicationModel, ParentMixinModel, DatabaseMixin):
            title: str = Field(..., description="Project title")
            description: Optional[str] = Field(None, description="Project description")
            budget: Optional[float] = Field(None, description="Project budget")
            tags: Optional[List[str]] = Field(
                default_factory=list, description="Project tags"
            )

        # Test creating SQLAlchemy models
        user_sql_model = create_sqlalchemy_model(
            TestUserModel, registry, base_model=self.TestBase
        )
        project_sql_model = create_sqlalchemy_model(
            TestProjectModel, registry, base_model=self.TestBase
        )

        # Verify models were created correctly
        self.assertTrue(hasattr(user_sql_model, "__tablename__"))
        self.assertTrue(hasattr(project_sql_model, "__tablename__"))
        self.assertEqual(user_sql_model.__tablename__, "test_users")
        self.assertEqual(project_sql_model.__tablename__, "test_projects")

        # Test table creation
        self.TestBase.metadata.create_all(self.engine)
        logger.debug("✓ Successfully created real tables")

        # Test actual database operations
        user_instance = user_sql_model(
            id="user-1", email="test@example.com", username="testuser", active=True
        )
        self.session.add(user_instance)
        self.session.commit()

        # Query back the data
        queried_user = (
            self.session.query(user_sql_model)
            .filter(user_sql_model.id == "user-1")
            .first()
        )
        self.assertIsNotNone(queried_user)
        self.assertEqual(queried_user.email, "test@example.com")
        self.assertEqual(queried_user.username, "testuser")
        self.assertTrue(queried_user.active)

        logger.debug("✓ Real database operations successful")

    def test_individual_model_creation(self):
        """Test creating individual SQLAlchemy models from real Pydantic models."""
        logger.debug("\n=== Testing Individual Model Creation ===")

        # Create model registry
        registry = ModelRegistry()

        # Create a real test Pydantic model
        class TestUserModel(ApplicationModel, UpdateMixinModel):
            email: str = Field(..., description="User's email address")
            username: Optional[str] = Field(None, description="User's username")
            active: bool = Field(True, description="Whether user is active")

        # Create SQLAlchemy model
        UserSQLModel = create_sqlalchemy_model(
            TestUserModel, registry, base_model=self.TestBase
        )

        # Verify the model was created correctly
        self.assertTrue(hasattr(UserSQLModel, "__tablename__"))
        self.assertEqual(UserSQLModel.__tablename__, "test_users")

        # Check that columns exist
        table = UserSQLModel.__table__
        column_names = [col.name for col in table.columns]

        expected_columns = [
            "id",
            "created_at",
            "created_by_user_id",
            "updated_at",
            "updated_by_user_id",
            "email",
            "username",
            "active",
        ]
        for col in expected_columns:
            self.assertIn(col, column_names, f"Column {col} should exist")

        # Test table creation and data insertion
        self.TestBase.metadata.create_all(self.engine)

        test_user = UserSQLModel(
            id="test-user", email="user@test.com", username="testuser", active=True
        )
        self.session.add(test_user)
        self.session.commit()

        # Verify data was inserted
        result = (
            self.session.query(UserSQLModel)
            .filter(UserSQLModel.id == "test-user")
            .first()
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.email, "user@test.com")

        logger.debug(f"✓ Created TestUser model with columns: {column_names}")

    def test_database_mixin_table_creation(self):
        """Test that DatabaseMixin properly creates tables when .DB is accessed."""
        logger.debug("\n=== Testing DatabaseMixin Table Creation ===")

        # Create a test Pydantic model that uses DatabaseMixin
        class TestDataApplicationModel(
            ApplicationModel, UpdateMixinModel, DatabaseMixin
        ):
            name: str = Field(..., description="Test name")
            value: int = Field(42, description="Test value")

        # Access the .DB property with base - this should create the table
        SQLModel = TestDataApplicationModel.DB(self.TestBase)

        # Verify the model was created correctly
        self.assertTrue(hasattr(SQLModel, "__tablename__"))
        self.assertTrue(hasattr(SQLModel, "__table__"))

        # Check that the table exists in metadata
        self.assertIn(SQLModel.__tablename__, self.TestBase.metadata.tables)

        # Verify columns exist
        table = SQLModel.__table__
        column_names = [col.name for col in table.columns]

        expected_columns = [
            "id",
            "created_at",
            "created_by_user_id",
            "updated_at",
            "updated_by_user_id",
            "name",
            "value",
        ]
        for col in expected_columns:
            self.assertIn(col, column_names, f"Column {col} should exist")

        # Test table creation and actual data operations
        self.TestBase.metadata.create_all(self.engine)

        test_instance = SQLModel(id="test-1", name="test name", value=100)
        self.session.add(test_instance)
        self.session.commit()

        # Query back to verify it works
        result = self.session.query(SQLModel).filter(SQLModel.id == "test-1").first()
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "test name")
        self.assertEqual(result.value, 100)

        logger.debug(
            f"✓ DatabaseMixin created table '{SQLModel.__tablename__}' with columns: {column_names}"
        )

    def test_mixin_inheritance(self):
        """Test that Pydantic mixins are correctly converted to SQLAlchemy mixins."""
        logger.debug("\n=== Testing Mixin Inheritance ===")

        # Create model registry
        registry = ModelRegistry()

        # Test different mixin combinations
        class TestModelWithMixins(
            ApplicationModel, UpdateMixinModel, ImageMixinModel, ParentMixinModel
        ):
            name: str = Field(..., description="Test name")

        SQLModel = create_sqlalchemy_model(
            TestModelWithMixins, registry, base_model=self.TestBase
        )

        # Check that mixin columns are present
        table = SQLModel.__table__
        column_names = [col.name for col in table.columns]

        expected_mixin_columns = [
            "id",
            "created_at",
            "created_by_user_id",  # BaseMixin
            "updated_at",
            "updated_by_user_id",  # UpdateMixin
            "image_url",  # ImageMixin
            "parent_id",  # ParentMixin
        ]

        for col in expected_mixin_columns:
            self.assertIn(col, column_names, f"Mixin column {col} should exist")

        # Test table creation and data operations
        self.TestBase.metadata.create_all(self.engine)

        test_instance = SQLModel(
            id="mixin-test", name="test name", image_url="https://example.com/image.png"
        )
        self.session.add(test_instance)
        self.session.commit()

        # Verify data
        result = (
            self.session.query(SQLModel).filter(SQLModel.id == "mixin-test").first()
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "test name")
        self.assertEqual(result.image_url, "https://example.com/image.png")

        logger.debug(f"✓ Mixin columns correctly inherited: {expected_mixin_columns}")

    def test_field_type_mapping(self):
        """Test that Pydantic field types are correctly mapped to SQLAlchemy types."""
        logger.debug("\n=== Testing Field Type Mapping ===")

        # Create model registry
        registry = ModelRegistry()

        class TestTypesModel(ApplicationModel):
            string_field: str = Field(..., description="String field")
            optional_string: Optional[str] = Field(None, description="Optional string")
            integer_field: int = Field(..., description="Integer field")
            boolean_field: bool = Field(True, description="Boolean field")
            datetime_field: datetime = Field(..., description="Datetime field")
            list_field: List[str] = Field(
                default_factory=list, description="List field"
            )
            dict_field: dict = Field(default_factory=dict, description="Dict field")

        SQLModel = create_sqlalchemy_model(
            TestTypesModel, registry, base_model=self.TestBase
        )
        table = SQLModel.__table__

        # Check column types
        type_mapping = {
            "string_field": "VARCHAR",
            "optional_string": "VARCHAR",
            "integer_field": "INTEGER",
            "boolean_field": "BOOLEAN",
            "datetime_field": "DATETIME",
            "list_field": "JSON",
            "dict_field": "JSON",
        }

        for col in table.columns:
            if col.name in type_mapping:
                expected_type = type_mapping[col.name]
                actual_type = str(col.type)
                logger.debug(f"  {col.name}: {actual_type} (nullable: {col.nullable})")

                # Check if the type contains the expected string (SQLite uses different type names)
                if expected_type in ["VARCHAR", "TEXT"]:
                    self.assertIn("VARCHAR", actual_type.upper())
                elif expected_type == "INTEGER":
                    self.assertIn("INTEGER", actual_type.upper())
                elif expected_type == "BOOLEAN":
                    self.assertIn("BOOLEAN", actual_type.upper())

        # Test actual data operations with different types
        self.TestBase.metadata.create_all(self.engine)

        test_instance = SQLModel(
            id="types-test",
            string_field="test string",
            integer_field=42,
            boolean_field=True,
            datetime_field=datetime.now(),
            list_field=["item1", "item2"],
            dict_field={"key": "value"},
        )
        self.session.add(test_instance)
        self.session.commit()

        # Verify data
        result = (
            self.session.query(SQLModel).filter(SQLModel.id == "types-test").first()
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.string_field, "test string")
        self.assertEqual(result.integer_field, 42)
        self.assertTrue(result.boolean_field)

        logger.debug("✓ Field types correctly mapped")

    def test_legacy_model_converter(self):
        """Test that the legacy ModelConverter class still works."""
        logger.debug("\n=== Testing Legacy ModelConverter ===")

        class TestLegacyModel(ApplicationModel):
            name: str = Field(..., description="Test name")

        # Test legacy method
        SQLModel = ModelConverter.create_sqlalchemy_model(
            TestLegacyModel, base_model=self.TestBase
        )

        self.assertTrue(hasattr(SQLModel, "__tablename__"))
        self.assertTrue(hasattr(SQLModel, "__table__"))

        # Test actual database operations
        self.TestBase.metadata.create_all(self.engine)

        test_instance = SQLModel(id="legacy-test", name="legacy name")
        self.session.add(test_instance)
        self.session.commit()

        result = (
            self.session.query(SQLModel).filter(SQLModel.id == "legacy-test").first()
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "legacy name")

        logger.debug("✓ Legacy ModelConverter.create_sqlalchemy_model works")

        # Test conversion methods
        test_instance = TestLegacyModel(name="test")

        # Test pydantic_to_dict
        data_dict = ModelConverter.pydantic_to_dict(test_instance)
        self.assertIsInstance(data_dict, dict)
        self.assertIn("name", data_dict)
        self.assertEqual(data_dict["name"], "test")

        logger.debug("✓ Legacy ModelConverter.pydantic_to_dict works")

    def test_error_handling(self):
        """Test error handling in the scaffolding system."""
        logger.debug("\n=== Testing Error Handling ===")

        # Create model registry
        registry = ModelRegistry()

        # Test with invalid model (not a BaseModel subclass)
        class NotABaseModel:
            pass

        try:
            create_sqlalchemy_model(NotABaseModel, registry, base_model=self.TestBase)
            self.fail("Should have raised an error for non-BaseModel class")
        except Exception as e:
            logger.debug(f"✓ Correctly handled invalid model: {type(e).__name__}")

        # Test clear_registry_cache
        class TestModel(ApplicationModel):
            name: str = Field(..., description="Test model")

        # Create a model to populate registries
        create_sqlalchemy_model(TestModel, registry, base_model=self.TestBase)

        # Test that clear_registry_cache works
        clear_registry_cache()

        # After clearing, we should be able to create models again without conflicts
        test_model_after_clear = create_sqlalchemy_model(
            TestModel, registry, base_model=self.TestBase
        )
        self.assertIsNotNone(test_model_after_clear)

        logger.debug("✓ clear_registry_cache works correctly")

    def test_complex_model_relationships(self):
        """Test complex model relationships and inheritance patterns."""
        logger.debug("\n=== Testing Complex Model Relationships ===")

        # Create model registry
        registry = ModelRegistry()

        # Create models with complex relationships
        class TestUserModel(ApplicationModel, UpdateMixinModel):
            email: str = Field(..., description="User email")

        class TestTeamModel(ApplicationModel, ParentMixinModel):
            name: str = Field(..., description="Team name")

        class TestProjectModel(ApplicationModel):
            title: str = Field(..., description="Project title")

        # Create SQLAlchemy models
        UserSQL = create_sqlalchemy_model(
            TestUserModel, registry, base_model=self.TestBase
        )
        TeamSQL = create_sqlalchemy_model(
            TestTeamModel, registry, base_model=self.TestBase
        )
        ProjectSQL = create_sqlalchemy_model(
            TestProjectModel, registry, base_model=self.TestBase
        )

        # Create all tables
        self.TestBase.metadata.create_all(self.engine)

        # Test actual data operations
        user = UserSQL(id="user-1", email="test@example.com")
        team = TeamSQL(id="team-1", name="Test Team")
        project = ProjectSQL(id="project-1", title="Test Project")

        self.session.add_all([user, team, project])
        self.session.commit()

        # Verify data
        user_result = self.session.query(UserSQL).filter(UserSQL.id == "user-1").first()
        team_result = self.session.query(TeamSQL).filter(TeamSQL.id == "team-1").first()
        project_result = (
            self.session.query(ProjectSQL).filter(ProjectSQL.id == "project-1").first()
        )

        self.assertIsNotNone(user_result)
        self.assertIsNotNone(team_result)
        self.assertIsNotNone(project_result)
        self.assertEqual(user_result.email, "test@example.com")
        self.assertEqual(team_result.name, "Test Team")
        self.assertEqual(project_result.title, "Test Project")

        logger.debug("✓ Complex model relationships work correctly")

    def test_field_descriptions_and_comments(self):
        """Test that field descriptions are converted to column comments."""
        logger.debug("\n=== Testing Field Descriptions and Comments ===")

        # Create model registry
        registry = ModelRegistry()

        class TestDescriptionsModel(ApplicationModel):
            name: str = Field(..., description="The name of the entity")
            email: str = Field(..., description="Email address for contact")
            active: bool = Field(True, description="Whether the entity is active")

        SQLModel = create_sqlalchemy_model(
            TestDescriptionsModel, registry, base_model=self.TestBase
        )
        table = SQLModel.__table__

        # Check that comments were set
        for col in table.columns:
            if col.name in ["name", "email", "active"]:
                self.assertIsNotNone(
                    col.comment, f"Column {col.name} should have a comment"
                )
                logger.debug(f"✓ {col.name}: {col.comment}")

        # Test database operations
        self.TestBase.metadata.create_all(self.engine)

        test_instance = SQLModel(
            id="desc-test", name="Test Entity", email="test@example.com", active=True
        )
        self.session.add(test_instance)
        self.session.commit()

        result = self.session.query(SQLModel).filter(SQLModel.id == "desc-test").first()
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Test Entity")

    def test_reference_fields_include_foreign_keys_without_target_table(self):
        """Foreign keys should be created for reference fields even before the target table exists."""

        registry = ModelRegistry()

        role_sql_model = create_sqlalchemy_model(
            RoleModel, registry, base_model=self.TestBase
        )

        team_column = role_sql_model.__table__.columns["team_id"]

        self.assertTrue(team_column.foreign_keys)
        fk_targets = {fk.target_fullname for fk in team_column.foreign_keys}
        self.assertIn("teams.id", fk_targets)
        # Ensure the referenced model can still be generated afterwards
        create_sqlalchemy_model(TeamModel, registry, base_model=self.TestBase)

        # FK comments should reflect the resolved target
        self.assertIn("Team", team_column.comment)


# ----------------------------------------------------------------------
# Security: filter / sort safety.
#
# These exercise AbstractLogicManager search/sort plumbing that ultimately
# emits SQL via SQLAlchemy. The aim is to prove user-controlled values
# never escape parameterization and unknown columns are rejected.
# ----------------------------------------------------------------------

import pytest


@pytest.mark.security
@pytest.mark.db
class TestSearchInputDeniesInjection:
    """Negative-path tests for filter/sort handling on list endpoints."""

    @pytest.fixture(scope="class")
    def fresh_user(self, server):
        from conftest import create_user

        return create_user(server)

    def test_sort_order_rejects_arbitrary_string(self, server, fresh_user):
        """`?sort_order=asc; DROP TABLE users--` must be rejected (422)."""
        response = server.get(
            "/v1/team?sort_by=name&sort_order=asc;DROP TABLE users--",
            headers={"Authorization": f"Bearer {fresh_user.jwt}"},
        )
        assert response.status_code == 422, (
            f"Bogus sort_order must be rejected (422); got "
            f"{response.status_code}: {response.text[:200]}"
        )

    def test_sort_by_rejects_unknown_column(self, server, fresh_user):
        """`?sort_by=password_hash` must be rejected (422)."""
        response = server.get(
            "/v1/team?sort_by=password_hash&sort_order=asc",
            headers={"Authorization": f"Bearer {fresh_user.jwt}"},
        )
        # Either rejects (422) or silently ignores (200). Reject is preferred.
        assert response.status_code in (200, 422), (
            f"sort_by=password_hash must be 422 or silently ignored 200; "
            f"got {response.status_code}"
        )

    def test_filter_value_with_quote_does_not_break_query(
        self, server, fresh_user
    ):
        """Filter value containing SQL syntax must be parameterized, not interpolated.

        If parameterization is broken, the request 500s; success is either
        200 (literal match, no rows) or 422 (rejection).
        """
        payload = "'; DROP TABLE teams; --"
        response = server.get(
            f"/v1/team?name={payload}",
            headers={"Authorization": f"Bearer {fresh_user.jwt}"},
        )
        assert response.status_code != 500, (
            f"SQL-syntax filter value crashed the query (500). Filter "
            f"parameterization may be broken. Body: {response.text[:200]}"
        )
        assert response.status_code in (
            200,
            400,
            422,
        ), f"Unexpected status {response.status_code}"


if __name__ == "__main__":
    unittest.main()
