import os
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from zephyrex.extensions.payment.PRV_Stripe_Payment import (
    Stripe_CustomerManager,
    Stripe_CustomerModel,
)
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.sqlalchemy import extension_model
from zephyrex.logic.AbstractLogicManager import HookContext, hook_bll
from zephyrex.logic.BLL_Auth import UserManager, UserModel


@extension_model(UserModel)
class Payment_UserModel(BaseModel):
    """Payment extension for User model with payment provider customer integration.

    Adds a provider-agnostic external_payment_id that links to the active
    payment provider's customer record (Stripe, Square, or PayPal).
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


def _customer_manager(requester_id: str) -> Stripe_CustomerManager:
    """A Stripe customer manager routed through the payment extension's root
    rotation (external managers take a rotation, not a model registry)."""
    from zephyrex.extensions.payment.EXT_Payment import EXT_Payment

    return Stripe_CustomerManager(
        requester_id=requester_id, rotation_manager=EXT_Payment.root
    )


@hook_bll(UserManager.login, timing="after")
def validate_subscription_on_login(context: HookContext):
    """
    Hook that validates user subscription after successful login.
    Throws HTTP 402 Payment Required if subscription is inactive.
    Updated to work with the Provider Rotation System.
    """
    # Skip validation for the root user. Both sides must be set: with
    # ROOT_EMAIL unset, a login whose data carries no email compared
    # None == None and bypassed the subscription check entirely.
    root_email = os.environ.get("ROOT_EMAIL")
    login_email = context.kwargs.get("login_data", {}).get("email")
    if root_email and login_email == root_email:
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

        try:
            user_manager = UserManager(
                model_registry=context.manager.model_registry, requester_id=user_id
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
    manager: UserManager, user_id: str, requester_id: Optional[str] = None
) -> dict:
    """
    Get comprehensive payment information for a user.

    Args:
        manager: A UserManager bound to the caller's registry
        user_id: ID of the user to get payment info for
        requester_id: ID of the requesting user (defaults to the manager's requester)

    Returns:
        Dict containing payment information
    """
    try:
        actual_requester_id = requester_id or manager.requester.id

        user = manager.get(id=user_id)

        payment_info = {
            "user_id": user_id,
            "has_payment_setup": False,
            "external_payment_id": None,
            "stripe_customer": None,
        }

        if hasattr(user, "external_payment_id") and user.external_payment_id:
            payment_info["has_payment_setup"] = True
            payment_info["external_payment_id"] = user.external_payment_id

            try:
                customer = _customer_manager(actual_requester_id).get(
                    id=user.external_payment_id
                )
                payment_info["stripe_customer"] = customer
            except Exception as e:
                logger.warning(f"Failed to get Stripe customer for user {user_id}: {e}")

        return payment_info

    except Exception as e:
        logger.error(f"Error getting payment info for user {user_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get user payment information"
        )


def get_user_subscription_status(
    manager: UserManager, user_id: str, requester_id: Optional[str] = None
) -> dict:
    """
    Get subscription status for a user via Provider Rotation System.

    Args:
        manager: A UserManager bound to the caller's registry
        user_id: ID of the user
        requester_id: ID of the requesting user (defaults to the manager's requester)

    Returns:
        Dict containing subscription status
    """
    try:
        actual_requester_id = requester_id or manager.requester.id

        payment_info = get_user_payment_info(
            manager, user_id=user_id, requester_id=actual_requester_id
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

            rotation_manager = EXT_Payment.root
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

            # Resolve via whichever provider the rotation system selects.
            # Each provider's SubscriptionModel exposes
            # get_subscription_status_via_provider with an identical contract.
            from zephyrex.extensions.payment.PRV_Stripe_Payment import (
                Stripe_SubscriptionModel,
            )

            return rotation_manager.rotate(  # type: ignore[no-any-return]
                Stripe_SubscriptionModel.get_subscription_status_via_provider,
                user_id=user_id,
            )
        except Exception as e:
            logger.error(f"Failed to fetch subscription status for user {user_id}: {e}")
            return {
                "subscription_id": None,
                "status": "unknown",
                "error": str(e),
            }

    except Exception as e:
        logger.error(f"Error getting subscription status for user {user_id}: {e}")
        return {"status": "unknown", "error": str(e)}
