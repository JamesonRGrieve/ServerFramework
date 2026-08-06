import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.extensions.auth_marketplace.DB_Auth_Marketplace import (  # MarketplaceListing,; MarketplaceTransaction,; MarketplaceVendor,
    TeamPaymentPortal,
    UserPaymentPortal,
)
from serverframework.lib.Dependencies import EXT_Dependency, PIP_Dependency
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


class EXT_Auth_Marketplace(AbstractStaticExtension):
    """
    Marketplace authentication extension for AGInfrastructure.

    Provides comprehensive marketplace authentication capabilities including vendor
    verification, transaction security, listing authentication, and marketplace access
    control. This extension provides static functionality and metadata to organize
    marketplace-related components and manage authentication workflows.

    The extension focuses on:
    - Vendor identity verification and authentication
    - Transaction security and payment processing integration
    - Marketplace listing authentication and validation
    - Access control for marketplace features
    - Reputation and trust management
    - Anti-fraud and security monitoring
    - Integration with payment providers

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "auth_marketplace"
    version = "1.0.0"
    description = (
        "Marketplace authentication extension for vendor verification, transaction security, "
        "and marketplace access control"
    )

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base authentication functionality and database operations",
        ),
        EXT_Dependency(
            name="auth",
            friendly_name="Authentication Extension",
            optional=False,
            reason="Required for integration with user authentication system",
        ),
        EXT_Dependency(
            name="auth_api_keys",
            friendly_name="API Keys Extension",
            optional=True,
            reason="Integration with API key authentication for marketplace APIs",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="stripe",
            friendly_name="Stripe Payment Processing",
            optional=True,
            reason="Integration with Stripe for secure payment processing",
            semver=">=6.0.0",
        ),
        PIP_Dependency(
            name="cryptography",
            friendly_name="Cryptography Library",
            optional=False,
            reason="Required for secure transaction signing and verification",
            semver=">=41.0.0",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for vendor verification and external service integration",
            semver=">=2.31.0",
        ),
        PIP_Dependency(
            name="redis",
            friendly_name="Redis Client",
            optional=True,
            reason="Session management and fraud detection for marketplace operations",
            semver=">=4.5.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables
    db_tables = [TeamPaymentPortal, UserPaymentPortal]

    # Define what capabilities this extension provides
    capabilities = [
        "vendor_verification",
        "transaction_security",
        "listing_authentication",
        "marketplace_access_control",
        "fraud_detection",
        "payment_integration",
        "reputation_management",
    ]

    # Vendor verification levels
    VERIFICATION_LEVELS = {
        "none": "No verification required",
        "basic": "Basic identity verification",
        "enhanced": "Enhanced business verification",
        "premium": "Premium KYC/KYB verification",
        "enterprise": "Enterprise-level verification",
    }

    # Transaction statuses
    TRANSACTION_STATUSES = {
        "pending": "Transaction is pending verification",
        "authorized": "Transaction is authorized",
        "completed": "Transaction completed successfully",
        "failed": "Transaction failed",
        "cancelled": "Transaction was cancelled",
        "disputed": "Transaction is under dispute",
        "refunded": "Transaction was refunded",
    }

    # Marketplace roles
    MARKETPLACE_ROLES = {
        "buyer": "Can purchase from marketplace",
        "vendor": "Can sell on marketplace",
        "moderator": "Can moderate marketplace content",
        "admin": "Full marketplace administration",
    }

    def __init__(
        self,
        enable_vendor_verification: bool = True,
        require_kyc_for_vendors: bool = True,
        enable_fraud_detection: bool = True,
        max_transaction_amount: float = 10000.0,
        enable_escrow: bool = True,
        escrow_release_days: int = 7,
        enable_reputation_system: bool = True,
        min_vendor_reputation: float = 3.0,
        enable_payment_integration: bool = True,
        stripe_api_key: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        # Configuration settings
        self.enable_vendor_verification = enable_vendor_verification
        self.require_kyc_for_vendors = require_kyc_for_vendors
        self.enable_fraud_detection = enable_fraud_detection
        self.max_transaction_amount = max_transaction_amount
        self.enable_escrow = enable_escrow
        self.escrow_release_days = escrow_release_days
        self.enable_reputation_system = enable_reputation_system
        self.min_vendor_reputation = min_vendor_reputation
        self.enable_payment_integration = enable_payment_integration
        self.stripe_api_key = stripe_api_key or env("STRIPE_SECRET_KEY")

        # Runtime state
        self.verified_vendors = {}
        self.active_transactions = {}
        self.reputation_cache = {}
        self.fraud_alerts = {}
        self.redis_client = None

    def on_initialize(self) -> bool:
        """
        Initialize the Marketplace extension.
        """
        logger.debug("Initializing Marketplace Extension...")

        try:
            # Initialize Redis for session management
            if self.enable_fraud_detection:
                self._initialize_redis()

            # Initialize payment processing
            if self.enable_payment_integration:
                self._initialize_payment_processing()

            # Register marketplace hooks
            self._register_marketplace_hooks()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            # Initialize fraud detection
            if self.enable_fraud_detection:
                self._initialize_fraud_detection()

            # Initialize reputation system
            if self.enable_reputation_system:
                self._initialize_reputation_system()

            logger.debug("Marketplace extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Marketplace extension: {str(e)}")
            return False

    def _initialize_redis(self):
        """Initialize Redis connection for session management."""
        try:
            import redis

            redis_url = env("REDIS_URL", "redis://localhost:6379/2")
            self.redis_client = redis.from_url(redis_url)

            # Test connection
            self.redis_client.ping()
            logger.debug("Redis connection established for marketplace sessions")

        except ImportError:
            logger.warning("Redis not available - using memory cache for sessions")
            self.redis_client = None
        except Exception as e:
            logger.warning(f"Redis connection failed: {e} - using memory cache")
            self.redis_client = None

    def _initialize_payment_processing(self):
        """Initialize payment processing integration."""
        try:
            if self.stripe_api_key:
                import stripe

                stripe.api_key = self.stripe_api_key
                logger.debug("Stripe payment processing initialized")
            else:
                logger.warning(
                    "Stripe API key not configured - payment processing disabled"
                )

        except ImportError:
            logger.warning("Stripe library not available - payment processing disabled")
        except Exception as e:
            logger.error(f"Error initializing payment processing: {e}")

    def _register_marketplace_hooks(self):
        """Register hooks for marketplace-related operations."""
        try:

            @AbstractExtension.bll_hook(
                "Marketplace", "VendorManager", "create", "before"
            )
            def validate_vendor_hook(manager, vendor_data, create_args=None):
                """Hook to validate vendors before registration."""
                try:
                    if (
                        hasattr(vendor_data, "business_email")
                        and vendor_data.business_email
                    ):
                        self._validate_vendor_requirements(vendor_data)
                        logger.debug("Vendor validation hook executed successfully")
                except Exception as e:
                    logger.error(f"Error in vendor validation hook: {e}")

            @AbstractExtension.bll_hook(
                "Marketplace", "TransactionManager", "create", "after"
            )
            def track_transaction_hook(manager, result, create_args=None):
                """Hook to track transaction creation."""
                try:
                    if self.enable_fraud_detection and result:
                        self._track_transaction_analytics(result)
                        logger.debug("Transaction tracking hook executed")
                except Exception as e:
                    logger.error(f"Error in transaction tracking hook: {e}")

        except Exception as e:
            logger.error(f"Error registering marketplace hooks: {e}")

    def _initialize_fraud_detection(self):
        """Initialize fraud detection system."""
        self.fraud_alerts = {
            "suspicious_transactions": [],
            "flagged_vendors": [],
            "blocked_ips": [],
            "velocity_violations": [],
        }

    def _initialize_reputation_system(self):
        """Initialize reputation tracking system."""
        self.reputation_cache = {
            "vendor_ratings": {},
            "buyer_ratings": {},
            "transaction_scores": {},
        }

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @AbstractExtension.ability("verify_vendor")
    async def verify_vendor(
        self,
        vendor_id: str,
        verification_level: str = "basic",
        verification_data: Optional[Dict[str, Any]] = None,
        force_reverification: bool = False,
    ) -> Dict[str, Any]:
        """
        Verify a vendor's identity and business credentials.
        """
        try:
            if not self.enable_vendor_verification:
                return {"success": False, "message": "Vendor verification not enabled"}

            # Check if already verified
            if vendor_id in self.verified_vendors and not force_reverification:
                existing_verification = self.verified_vendors[vendor_id]
                return {
                    "success": True,
                    "vendor_id": vendor_id,
                    "verification": existing_verification,
                    "message": "Vendor already verified",
                }

            verification_data = verification_data or {}

            # Perform verification based on level
            verification_result = await self._perform_verification(
                vendor_id, verification_level, verification_data
            )

            if verification_result["success"]:
                # Store verification result
                verification_record = {
                    "vendor_id": vendor_id,
                    "verification_level": verification_level,
                    "status": "verified",
                    "verified_at": datetime.utcnow().isoformat(),
                    "expires_at": (datetime.utcnow() + timedelta(days=365)).isoformat(),
                    "verification_data": verification_data,
                    "verification_score": verification_result.get("score", 0),
                }

                self.verified_vendors[vendor_id] = verification_record

                logger.debug(
                    f"Vendor {vendor_id} verified at {verification_level} level"
                )
                return {
                    "success": True,
                    "vendor_id": vendor_id,
                    "verification": verification_record,
                    "message": "Vendor verification successful",
                }
            else:
                return {
                    "success": False,
                    "vendor_id": vendor_id,
                    "message": verification_result.get(
                        "message", "Verification failed"
                    ),
                }

        except Exception as e:
            logger.error(f"Error verifying vendor: {e}")
            return {
                "success": False,
                "message": f"Vendor verification failed: {str(e)}",
            }

    @AbstractExtension.ability("authenticate_transaction")
    async def authenticate_transaction(
        self,
        transaction_data: Dict[str, Any],
        require_escrow: bool = True,
        fraud_check: bool = True,
    ) -> Dict[str, Any]:
        """
        Authenticate and authorize a marketplace transaction.
        """
        try:
            # Extract transaction details
            buyer_id = transaction_data.get("buyer_id")
            vendor_id = transaction_data.get("vendor_id")
            amount = transaction_data.get("amount", 0)
            listing_id = transaction_data.get("listing_id")

            # Validate transaction data
            if not all([buyer_id, vendor_id, amount, listing_id]):
                return {"success": False, "message": "Invalid transaction data"}

            # Check transaction amount limits
            if amount > self.max_transaction_amount:
                return {"success": False, "message": "Transaction amount exceeds limit"}

            # Verify vendor
            if vendor_id not in self.verified_vendors:
                return {"success": False, "message": "Vendor not verified"}

            # Check vendor reputation
            if self.enable_reputation_system:
                vendor_reputation = await self._get_vendor_reputation(vendor_id)
                if vendor_reputation < self.min_vendor_reputation:
                    return {"success": False, "message": "Vendor reputation too low"}

            # Fraud detection
            if fraud_check and self.enable_fraud_detection:
                fraud_result = await self._check_transaction_fraud(transaction_data)
                if fraud_result["is_suspicious"]:
                    return {
                        "success": False,
                        "message": "Transaction flagged for fraud",
                        "fraud_details": fraud_result,
                    }

            # Generate transaction ID
            import uuid

            transaction_id = str(uuid.uuid4())

            # Create transaction record
            transaction_record = {
                "id": transaction_id,
                "buyer_id": buyer_id,
                "vendor_id": vendor_id,
                "listing_id": listing_id,
                "amount": amount,
                "status": "authorized",
                "created_at": datetime.utcnow().isoformat(),
                "escrow_enabled": require_escrow and self.enable_escrow,
                "fraud_checked": fraud_check,
            }

            # Store transaction
            self.active_transactions[transaction_id] = transaction_record

            logger.debug(f"Transaction {transaction_id} authenticated successfully")
            return {
                "success": True,
                "transaction_id": transaction_id,
                "transaction": transaction_record,
                "message": "Transaction authenticated",
            }

        except Exception as e:
            logger.error(f"Error authenticating transaction: {e}")
            return {
                "success": False,
                "message": f"Transaction authentication failed: {str(e)}",
            }

    @AbstractExtension.ability("process_payment")
    async def process_payment(
        self,
        transaction_id: str,
        payment_method: str = "stripe",
        payment_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process payment for a marketplace transaction.
        """
        try:
            if not self.enable_payment_integration:
                return {"success": False, "message": "Payment processing not enabled"}

            # Get transaction
            if transaction_id not in self.active_transactions:
                return {"success": False, "message": "Transaction not found"}

            transaction = self.active_transactions[transaction_id]

            if transaction["status"] != "authorized":
                return {
                    "success": False,
                    "message": f"Transaction status is {transaction['status']}",
                }

            payment_data = payment_data or {}

            # Process payment based on method
            if payment_method == "stripe" and self.stripe_api_key:
                payment_result = await self._process_stripe_payment(
                    transaction, payment_data
                )
            else:
                return {
                    "success": False,
                    "message": f"Payment method {payment_method} not supported",
                }

            if payment_result["success"]:
                # Update transaction status
                transaction["status"] = (
                    "completed" if not transaction.get("escrow_enabled") else "pending"
                )
                transaction["payment_id"] = payment_result.get("payment_id")
                transaction["payment_method"] = payment_method
                transaction["paid_at"] = datetime.utcnow().isoformat()

                # Handle escrow if enabled
                if transaction.get("escrow_enabled"):
                    escrow_result = await self._setup_escrow(transaction_id)
                    transaction["escrow_id"] = escrow_result.get("escrow_id")

                logger.debug(f"Payment processed for transaction {transaction_id}")
                return {
                    "success": True,
                    "transaction_id": transaction_id,
                    "payment_id": payment_result.get("payment_id"),
                    "status": transaction["status"],
                }
            else:
                transaction["status"] = "failed"
                return {
                    "success": False,
                    "message": payment_result.get("message", "Payment failed"),
                }

        except Exception as e:
            logger.error(f"Error processing payment: {e}")
            return {"success": False, "message": f"Payment processing failed: {str(e)}"}

    @AbstractExtension.ability("validate_listing")
    async def validate_listing(
        self,
        listing_data: Dict[str, Any],
        vendor_id: str,
    ) -> Dict[str, Any]:
        """
        Validate a marketplace listing for authenticity and compliance.
        """
        try:
            # Check vendor verification
            if vendor_id not in self.verified_vendors:
                return {
                    "success": False,
                    "message": "Vendor must be verified to create listings",
                }

            # Validate listing data
            required_fields = ["title", "description", "price", "category"]
            missing_fields = [
                field for field in required_fields if not listing_data.get(field)
            ]

            if missing_fields:
                return {
                    "success": False,
                    "message": f"Missing required fields: {', '.join(missing_fields)}",
                }

            # Content validation
            validation_result = await self._validate_listing_content(listing_data)
            if not validation_result["is_valid"]:
                return {
                    "success": False,
                    "message": validation_result.get(
                        "message", "Listing content validation failed"
                    ),
                }

            # Generate listing signature
            listing_signature = self._generate_listing_signature(
                listing_data, vendor_id
            )

            logger.debug(f"Listing validated for vendor {vendor_id}")
            return {
                "success": True,
                "listing_data": listing_data,
                "vendor_id": vendor_id,
                "signature": listing_signature,
                "validated_at": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error validating listing: {e}")
            return {"success": False, "message": f"Listing validation failed: {str(e)}"}

    @AbstractExtension.ability("check_marketplace_access")
    async def check_marketplace_access(
        self,
        user_id: str,
        requested_role: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Check user access permissions for marketplace operations.
        """
        try:
            context = context or {}

            # Get user marketplace roles
            user_roles = await self._get_user_marketplace_roles(user_id)

            # Check if user has requested role
            has_access = requested_role in user_roles or "admin" in user_roles

            if not has_access:
                return {
                    "success": False,
                    "message": f"User does not have {requested_role} access",
                    "user_roles": user_roles,
                }

            # Additional context-based checks
            access_result = await self._perform_context_access_check(
                user_id, requested_role, context
            )

            if not access_result["allowed"]:
                return {
                    "success": False,
                    "message": access_result.get("message", "Access denied"),
                }

            return {
                "success": True,
                "user_id": user_id,
                "requested_role": requested_role,
                "user_roles": user_roles,
                "access_granted": True,
            }

        except Exception as e:
            logger.error(f"Error checking marketplace access: {e}")
            return {"success": False, "message": f"Access check failed: {str(e)}"}

    @AbstractExtension.ability("update_reputation")
    async def update_reputation(
        self,
        user_id: str,
        user_type: str,  # "vendor" or "buyer"
        rating: float,
        transaction_id: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update user reputation based on transaction feedback.
        """
        try:
            if not self.enable_reputation_system:
                return {"success": False, "message": "Reputation system not enabled"}

            if not 1.0 <= rating <= 5.0:
                return {
                    "success": False,
                    "message": "Rating must be between 1.0 and 5.0",
                }

            # Get current reputation
            reputation_key = f"{user_type}_ratings"
            current_reputation = self.reputation_cache[reputation_key].get(
                user_id,
                {
                    "total_rating": 0.0,
                    "rating_count": 0,
                    "average_rating": 0.0,
                },
            )

            # Update reputation
            new_total = current_reputation["total_rating"] + rating
            new_count = current_reputation["rating_count"] + 1
            new_average = new_total / new_count

            updated_reputation = {
                "total_rating": new_total,
                "rating_count": new_count,
                "average_rating": new_average,
                "last_updated": datetime.utcnow().isoformat(),
            }

            self.reputation_cache[reputation_key][user_id] = updated_reputation

            # Store feedback if provided
            if feedback and transaction_id:
                feedback_record = {
                    "transaction_id": transaction_id,
                    "user_id": user_id,
                    "user_type": user_type,
                    "rating": rating,
                    "feedback": feedback,
                    "created_at": datetime.utcnow().isoformat(),
                }
                # In real implementation, this would be stored in database

            logger.debug(
                f"Updated {user_type} reputation for {user_id}: {new_average:.2f}"
            )
            return {
                "success": True,
                "user_id": user_id,
                "user_type": user_type,
                "reputation": updated_reputation,
            }

        except Exception as e:
            logger.error(f"Error updating reputation: {e}")
            return {"success": False, "message": f"Reputation update failed: {str(e)}"}

    # Helper methods
    async def _perform_verification(
        self, vendor_id: str, level: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Perform vendor verification based on level."""
        try:
            if level == "basic":
                # Basic email and phone verification
                return {"success": True, "score": 70}
            elif level == "enhanced":
                # Business document verification
                return {"success": True, "score": 85}
            elif level == "premium":
                # Full KYC/KYB verification
                return {"success": True, "score": 95}
            else:
                return {
                    "success": False,
                    "message": f"Unknown verification level: {level}",
                }

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def _get_vendor_reputation(self, vendor_id: str) -> float:
        """Get vendor reputation score."""
        vendor_rep = self.reputation_cache["vendor_ratings"].get(vendor_id)
        return vendor_rep["average_rating"] if vendor_rep else 5.0

    async def _check_transaction_fraud(
        self, transaction_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check transaction for fraud indicators."""
        try:
            # Simple fraud detection logic
            amount = transaction_data.get("amount", 0)

            # Flag high-value transactions
            if amount > self.max_transaction_amount * 0.8:
                return {"is_suspicious": True, "reason": "High transaction amount"}

            # Check for velocity violations
            # In real implementation, this would check recent transaction patterns

            return {"is_suspicious": False}

        except Exception as e:
            logger.error(f"Error in fraud check: {e}")
            return {"is_suspicious": True, "reason": "Fraud check failed"}

    async def _process_stripe_payment(
        self, transaction: Dict[str, Any], payment_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process payment through Stripe."""
        try:
            import stripe

            # Create payment intent
            intent = stripe.PaymentIntent.create(
                amount=int(transaction["amount"] * 100),  # Convert to cents
                currency="usd",
                metadata={
                    "transaction_id": transaction["id"],
                    "vendor_id": transaction["vendor_id"],
                    "buyer_id": transaction["buyer_id"],
                },
            )

            return {
                "success": True,
                "payment_id": intent.id,
                "client_secret": intent.client_secret,
            }

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def _setup_escrow(self, transaction_id: str) -> Dict[str, Any]:
        """Setup escrow for transaction."""
        import uuid

        escrow_id = str(uuid.uuid4())

        # In real implementation, this would integrate with escrow service
        return {"escrow_id": escrow_id}

    async def _validate_listing_content(
        self, listing_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate listing content for policy compliance."""
        try:
            # Simple content validation
            title = listing_data.get("title", "")
            description = listing_data.get("description", "")

            # Check for prohibited content
            prohibited_terms = ["illegal", "counterfeit", "stolen"]
            content_text = f"{title} {description}".lower()

            for term in prohibited_terms:
                if term in content_text:
                    return {
                        "is_valid": False,
                        "message": f"Listing contains prohibited content: {term}",
                    }

            return {"is_valid": True}

        except Exception as e:
            return {"is_valid": False, "message": str(e)}

    def _generate_listing_signature(
        self, listing_data: Dict[str, Any], vendor_id: str
    ) -> str:
        """Generate cryptographic signature for listing."""
        import json

        content = json.dumps({**listing_data, "vendor_id": vendor_id}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    async def _get_user_marketplace_roles(self, user_id: str) -> List[str]:
        """Get user's marketplace roles."""
        # In real implementation, this would query the database
        return ["buyer"]  # Default role

    async def _perform_context_access_check(
        self, user_id: str, role: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Perform additional context-based access checks."""
        return {"allowed": True}

    def _validate_vendor_requirements(self, vendor_data: Any):
        """Validate vendor requirements (used in hooks)."""
        # Implementation for hook validation
        pass

    def _track_transaction_analytics(self, result: Any):
        """Track transaction analytics (used in hooks)."""
        # Implementation for hook tracking
        pass

    # Extension lifecycle methods
    def on_start(self) -> bool:
        """Start the Marketplace extension."""
        try:
            # Reconnect to Redis if needed
            if self.enable_fraud_detection and not self.redis_client:
                self._initialize_redis()

            # Reinitialize payment processing
            if self.enable_payment_integration:
                self._initialize_payment_processing()

            logger.debug("Marketplace extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start Marketplace extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Marketplace extension."""
        try:
            # Clean up resources
            self.verified_vendors.clear()
            self.active_transactions.clear()
            self.reputation_cache.clear()
            self.fraud_alerts.clear()

            # Close Redis connection
            if self.redis_client:
                self.redis_client.close()

            logger.debug("Marketplace extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping Marketplace extension: {e}")
            return False

    def on_startup(self):
        """Called during application startup."""
        logger.debug("Marketplace extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("Marketplace extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import cryptography
        except ImportError:
            issues.append(
                "Cryptography library not installed - transaction security will not work"
            )

        try:
            import requests
        except ImportError:
            issues.append(
                "Requests library not installed - vendor verification will not work"
            )

        # Check payment processing configuration
        if self.enable_payment_integration:
            if not self.stripe_api_key:
                issues.append(
                    "Stripe API key not configured but payment processing is enabled"
                )
            else:
                try:
                    import stripe
                except ImportError:
                    issues.append(
                        "Stripe library not available but payment processing is enabled"
                    )

        # Check Redis connection if fraud detection enabled
        if self.enable_fraud_detection and not self.redis_client:
            try:
                import redis

                redis_url = env("REDIS_URL")
                if not redis_url:
                    issues.append(
                        "Redis URL not configured but fraud detection is enabled"
                    )
            except ImportError:
                issues.append("Redis not available but fraud detection is enabled")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "marketplace:read",
            "marketplace:write",
            "vendors:verify",
            "vendors:manage",
            "transactions:create",
            "transactions:process",
            "listings:validate",
            "payments:process",
            "reputation:update",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_verification_levels(self) -> Dict[str, str]:
        """Get available verification levels."""
        return self.VERIFICATION_LEVELS.copy()

    def get_transaction_statuses(self) -> Dict[str, str]:
        """Get available transaction statuses."""
        return self.TRANSACTION_STATUSES.copy()

    def get_marketplace_roles(self) -> Dict[str, str]:
        """Get available marketplace roles."""
        return self.MARKETPLACE_ROLES.copy()

    def get_marketplace_stats(self) -> Dict[str, Any]:
        """Get marketplace statistics."""
        return {
            "verified_vendors": len(self.verified_vendors),
            "active_transactions": len(self.active_transactions),
            "fraud_alerts": len(self.fraud_alerts.get("suspicious_transactions", [])),
            "reputation_enabled": self.enable_reputation_system,
            "escrow_enabled": self.enable_escrow,
            "payment_integration_enabled": self.enable_payment_integration,
        }
