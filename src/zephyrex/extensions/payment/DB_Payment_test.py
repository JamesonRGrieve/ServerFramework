import pytest
from faker import Faker
from typing import Any, Dict, List, Optional

from zephyrex.database.DB_Auth_test import TestUser as CoreUserTests
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.payment.EXT_Payment import EXT_Payment
from zephyrex.lib.Environment import env

# Import BLL models and use their .DB property for SQLAlchemy models

faker = Faker()


class TestPayment_User(CoreUserTests, ExtensionServerMixin):
    """
    Test User database functionality with payment extension.
    Tests the same functionality as TestUser in DB_Auth_test.py,
    but with payment extension enabled to ensure core functionality still works
    with the payment extension field (external_payment_id).
    """

    # Extension configuration for ExtensionServerMixin
    extension_class = EXT_Payment

    @property
    def create_fields(self):
        return {
            **super().create_fields,
            "external_payment_id": "abc123",  # Payment extension field
        }

    @property
    def update_fields(self):
        return {
            **super().update_fields,
            "external_payment_id": "xyz456",
        }

    def test_unique_constraint_with_payment_field(
        self, db, admin_a, team_a, server, model_registry
    ):
        """Test that unique constraints work correctly with payment extension field - verifies constraints still work"""
        self.db = db
        self._server = server
        self.model_registry = model_registry
        self.ensure_model(server)

        # Create first user
        user1_email = faker.unique.email()
        user1_data = {
            **{k: v() if callable(v) else v for k, v in self.create_fields.items()},
            "email": user1_email,
            "external_payment_id": "cus_unique_test_customer",
        }

        created_user1 = self._CRUD_create_with_data(
            user1_data, "dict", admin_a.id, team_a.id, "unique_user1"
        )

        # Try to create second user with same email (should fail due to unique constraint)
        user2_data = {
            **{k: v() if callable(v) else v for k, v in self.create_fields.items()},
            "email": user1_email,  # Same email - should trigger unique constraint
            "external_payment_id": "cus_different_customer",
        }

        # This should raise an exception due to unique email constraint
        try:
            self._CRUD_create_with_data(
                user2_data, "dict", admin_a.id, team_a.id, "unique_user2"
            )
        except Exception as e:
            logger.debug(f"Exception raised as expected: {e}")
            raise

    def test_null_payment_field_handling(
        self, db, admin_a, team_a, server, model_registry
    ):
        """Test that NULL values in payment field are handled correctly - verifies NULL handling"""
        self.db = db
        self._server = server
        self.model_registry = model_registry
        self.ensure_model(server)

        # Create user with NULL payment ID
        user_data = {
            **{k: v() if callable(v) else v for k, v in self.create_fields.items()},
            "external_payment_id": None,
        }

        created_user = self._CRUD_create_with_data(
            user_data, "dict", admin_a.id, team_a.id, "CRUD_update"
        )

        # Verify NULL is handled correctly
        assert created_user["external_payment_id"] is None

        # User records self-set ``created_by_user_id`` to the new user's id, so
        # admin_a has no permission claim on the row. Drive the update as the
        # new user themselves, mirroring the override pattern in the DB-layer
        # ``test_CRUD_update``.
        new_user_id = created_user["id"]

        # Update to a value and back to NULL
        data = {"external_payment_id": "cus_temp_customer"}

        updated_user = self._CRUD_update(
            "dict",
            new_user_id,
            team_a.id,
            update_data=data,
        )
        assert updated_user["external_payment_id"] == "cus_temp_customer"

        # Update back to NULL
        data_null = {"external_payment_id": None}
        updated_user_null = self._CRUD_update(
            "dict",
            new_user_id,
            team_a.id,
            update_data=data_null,
        )
        assert updated_user_null["external_payment_id"] is None

    def _CRUD_create_with_data(
        self,
        data: Dict[str, Any],
        return_type: str = "dict",
        user_id: str = env("ROOT_ID"),
        team_id: Optional[str] = None,
        key="CRUD_create_dict",
    ):
        """
        Helper method to create entity with specific data, extending the base _CRUD_create logic.
        """
        key = key.replace("dict", return_type)

        # Get model registry and server from test context
        model_registry = self._get_model_registry()
        server = getattr(self, "_server", None)

        # Get base entity data using existing logic
        entity_data_list = self.build_entities(
            server, user_id, team_id, unique_fields=self.unique_fields
        )
        create_data = entity_data_list[0] if entity_data_list else {}

        # If build_entities didn't provide data, start with create_fields
        if not create_data and self.create_fields:
            create_data = self.create_fields.copy()

            # Resolve callable values
            for field, value in create_data.items():
                if callable(value):
                    create_data[field] = value()

        # Merge provided data with create_data (provided data takes precedence)
        create_data.update(data)

        # Create entity with merged data
        self.tracked_entities[key] = self.sqlalchemy_model.create(
            env("SYSTEM_ID") if self.is_system_entity else user_id,
            model_registry,
            return_type=return_type,
            **create_data,
        )

        return self.tracked_entities[key]
