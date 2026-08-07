import os
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from zephyrex.database.DatabaseManager import DatabaseManager
from zephyrex.extensions.payment.PRV_Stripe_Payment import (
    Stripe_CustomerManager,
    Stripe_CustomerModel,
)
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic2SQLAlchemy import extension_model
from zephyrex.logic.AbstractLogicManager import HookContext, hook_bll
from zephyrex.logic.BLL_Auth import UserManager, UserModel


@extension_model(UserModel)
class Payment_UserModel(BaseModel):
    """
    Payment extension for User model with Stripe customer integration.

    This automatically adds:
    - external_payment_id: Optional[str] field
    - stripe_customer: Optional[Stripe_CustomerModel] navigation property

    The navigation property resolves automatically when accessed:
    user.stripe_customer -> calls Stripe API to get customer details
    """

    external_payment_id: Optional[str] = Field(
        None, description="External payment ID linking to payment provider customer"
    )
    stripe_customer: Optional[Stripe_CustomerModel] = Field(
        None, description="Stripe customer navigation property"
    )

    class Create(BaseModel):
        external_payment_id: Optional[str] = Field(
            None, description="External payment ID linking to payment provider customer"
        )

    class Update(BaseModel):
        external_payment_id: Optional[str] = Field(
            None, description="External payment ID linking to payment provider customer"
        )

    class Search(BaseModel):
        external_payment_id: Optional[str] = Field(
            None, description="External payment ID linking to payment provider customer"
        )


# Extension model will be automatically discovered and applied by ModelRegistry
# when it imports extension modules - no need for immediate application


def get_or_create_payment_customer(
    self,
    user_id: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """
    Get or create a payment customer for a user.
    This is properly injected as an instance method.

    Args:
        self: The UserManager instance
        user_id: ID of the user
        email: User's email (optional, will get from user if not provided)
        name: User's name (optional, will get from user if not provided)

    Returns:
        Dict containing customer information

    Raises:
        HTTPException: If user not found or customer creation fails
    """
    try:
        user = self.get(id=user_id)

        if hasattr(user, "external_payment_id") and user.external_payment_id:
            return {
                "customer_id": user.external_payment_id,
                "created": False,
                "user_id": user_id,
            }

        customer_email = email or user.email
        customer_name = (
            name or user.display_name or f"{user.first_name} {user.last_name}".strip()
        )

        # Create Stripe customer manager with proper parameters
        stripe_customer_manager = Stripe_CustomerManager(
            requester_id=user_id,
            db_manager=self.db_manager,
            db=self.db,
            model_registry=getattr(self, "model_registry", None),
        )

        # Use the standard create method with proper field names
        customer = stripe_customer_manager.create(
            email=customer_email,
            name=customer_name,
            metadata={"user_id": user_id},
        )

        if customer and hasattr(customer, "id"):
            # Update user with the Stripe customer ID
            self.update(id=user_id, external_payment_id=customer.id)

            logger.info(f"Created Stripe customer {customer.id} for user {user_id}")

            return {
                "customer_id": customer.id,
                "created": True,
                "user_id": user_id,
            }
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create payment customer: No customer returned",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting or creating payment customer for user {user_id}: {e}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to get or create payment customer"
        )


# Properly inject as an instance method
UserManager.get_or_create_payment_customer = get_or_create_payment_customer


@hook_bll(UserManager.login, timing="after")
def validate_subscription_on_login(context: HookContext):
    """
    Hook that validates user subscription after successful login.
    Throws HTTP 402 Payment Required if subscription is inactive.
    Updated to work with the Provider Rotation System.
    """
    # Skip validation for system users
    if context.kwargs.get("login_data", {}).get("email") == os.environ.get(
        "ROOT_EMAIL"
    ):
        return

    # Skip if subscription validation is disabled
    if os.environ.get("DISABLE_SUBSCRIPTION_VALIDATION", "false").lower() == "true":
        return

    try:
        # Get user ID from login result
        result = context.result
        if not result or not result.get("id"):
            return

        user_id = result["id"]

        # Get the database manager from the original manager context
        db_manager = context.manager.db_manager
        db = context.kwargs.get("db") or context.manager.db

        # Create user manager with proper parameters
        try:
            user_manager = UserManager(
                requester_id=user_id,
                db_manager=db_manager,
                db=db,
                model_registry=(
                    context.manager.model_registry
                    if hasattr(context.manager, "model_registry")
                    else None
                ),
            )
            user = user_manager.get(id=user_id)
        except HTTPException as e:
            if e.status_code == 404:
                # User not found - skip validation
                return
            raise

        # Skip validation if user doesn't have external payment ID
        if not hasattr(user, "external_payment_id") or not user.external_payment_id:
            logger.debug(
                f"User {user_id} has no external payment ID, skipping validation"
            )
            return

        # Check subscription status via Provider Rotation System
        try:
            subscription_status = get_user_subscription_status(
                user_manager, user_id=user_id, requester_id=user_id
            )
        except Exception as e:
            logger.warning(f"Failed to check subscription for user {user_id}: {e}")
            # Don't block login if subscription check fails
            return

        # If subscription is inactive, prevent login
        if subscription_status.get("status") == "inactive":
            raise HTTPException(
                status_code=402,  # Payment Required
                detail="Your subscription is inactive. Please update your payment method.",
            )

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error in subscription validation hook: {e}")
        # Don't block login for other errors
        return


def get_user_payment_info(
    self, user_id: str, requester_id: Optional[str] = None
) -> dict:
    """
    Get comprehensive payment information for a user.
    This is properly implemented as an instance method.

    Args:
        self: The UserManager instance
        user_id: ID of the user to get payment info for
        requester_id: ID of the requesting user (optional, uses self.requester.id if not provided)

    Returns:
        Dict containing payment information
    """
    try:
        # Use the requester_id from the manager if not provided
        actual_requester_id = requester_id or self.requester.id

        user = self.get(id=user_id)

        # Get basic user info
        payment_info = {
            "user_id": user_id,
            "has_payment_setup": False,
            "external_payment_id": None,
            "stripe_customer": None,
        }

        # Check if user has payment setup
        if hasattr(user, "external_payment_id") and user.external_payment_id:
            payment_info["has_payment_setup"] = True
            payment_info["external_payment_id"] = user.external_payment_id

            # Try to get Stripe customer details
            try:
                stripe_manager = Stripe_CustomerManager(
                    requester_id=actual_requester_id,
                    db_manager=self.db_manager,
                    db=self.db,
                    model_registry=getattr(self, "model_registry", None),
                )
                # Use the standard get method with proper parameter
                customer = stripe_manager.get(id=user.external_payment_id)
                payment_info["stripe_customer"] = customer
            except Exception as e:
                logger.warning(f"Failed to get Stripe customer for user {user_id}: {e}")

        return payment_info

    except Exception as e:
        logger.error(f"Error getting payment info for user {user_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get user payment information"
        )


# Properly inject as an instance method
UserManager.get_user_payment_info = get_user_payment_info


def get_user_subscription_status(
    self, user_id: str, requester_id: Optional[str] = None
) -> dict:
    """
    Get subscription status for a user via Provider Rotation System.
    This is properly implemented as an instance method.

    Args:
        self: The UserManager instance
        user_id: ID of the user
        requester_id: ID of the requesting user (optional, uses self.requester.id if not provided)

    Returns:
        Dict containing subscription status
    """
    try:
        # Use the requester_id from the manager if not provided
        actual_requester_id = requester_id or self.requester.id

        # Get user payment info
        payment_info = self.get_user_payment_info(
            user_id=user_id, requester_id=actual_requester_id
        )

        if not payment_info["has_payment_setup"]:
            return {"status": "inactive", "reason": "No payment method setup"}

        # Item 74 — call the real rotation entrypoint instead of returning a
        # hardcoded mock. The Payment extension's root rotation routes the
        # call to the active payment provider's
        # ``get_subscription_status_via_provider``; failures surface here as
        # the typed external errors from Item 1's hierarchy (transient/
        # rate-limit errors are retried/backed-off by the rotation system
        # before they reach this layer).
        try:
            from zephyrex.extensions.payment.EXT_Payment import EXT_Payment
            from zephyrex.extensions.payment.PRV_Stripe_Payment import (
                Stripe_SubscriptionModel,
            )

            rotation_manager = getattr(EXT_Payment, "root", None)
            if rotation_manager is None:
                logger.warning(
                    "Payment extension root rotation unavailable; "
                    "returning unknown subscription status for user %s",
                    user_id,
                )
                return {
                    "subscription_id": None,
                    "status": "unknown",
                    "reason": "Payment rotation not configured",
                }

            return rotation_manager.rotate(  # type: ignore[no-any-return]
                Stripe_SubscriptionModel.get_subscription_status_via_provider,
                user_id=user_id,
            )
        except Exception as e:
            logger.error(
                f"Failed to fetch subscription status for user {user_id}: {e}"
            )
            return {
                "subscription_id": None,
                "status": "unknown",
                "error": str(e),
            }

    except Exception as e:
        logger.error(f"Error getting subscription status for user {user_id}: {e}")
        return {"status": "unknown", "error": str(e)}


# Properly inject as an instance method
UserManager.get_user_subscription_status = get_user_subscription_status
