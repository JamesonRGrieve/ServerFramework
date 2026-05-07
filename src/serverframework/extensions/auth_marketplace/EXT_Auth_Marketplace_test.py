from typing import List
from unittest.mock import MagicMock, patch

import pytest

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from serverframework.extensions.AbstractEXTTest import AbstractEXTTest
from serverframework.extensions.auth_marketplace.EXT_Auth_Marketplace import EXT_Auth_Marketplace
from serverframework.lib.Dependencies import install_pip_dependencies


class TestEXTAuthMarketplace(AbstractEXTTest):
    """
    Test suite for EXT_Auth_Marketplace extension.

    Tests extension initialization, marketplace authentication capabilities, abilities, and
    vendor verification functionality. Focuses on testing vendor verification, transaction
    security, payment integration, and fraud detection rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - Marketplace capabilities and abilities (vendor verification, transaction auth, payment processing)
    - Payment integration (Stripe) and Redis integration
    - Vendor verification and reputation management
    - Transaction security and fraud detection
    - Listing authentication and access control
    - Escrow and marketplace workflows
    """

    # Configure the test class
    extension_class = EXT_Auth_Marketplace
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
        cleanup=True,
    )

    # Expected extension properties
    expected_abilities = [
        "verify_vendor",
        "authenticate_transaction",
        "process_payment",
        "validate_listing",
        "check_marketplace_access",
        "update_reputation",
    ]

    expected_capabilities = [
        "vendor_verification",
        "transaction_security",
        "listing_authentication",
        "marketplace_access_control",
        "fraud_detection",
        "payment_integration",
        "reputation_management",
    ]

    # Tests to skip
    _skip_tests: List[SkipThisTest] = []

    @pytest.mark.dependency(name="auth_marketplace_dependencies")
    def test_install_pip_dependencies(self):
        """
        Install PIP dependencies required by the Auth Marketplace extension.
        This test must run first and all other Auth Marketplace tests depend on it.
        """
        # Get the pip dependencies from the extension class
        pip_deps = self.extension_class.pip_dependencies

        # Install the dependencies
        result = install_pip_dependencies(pip_deps, only_missing=True)

        # Check final satisfaction status after installation attempt
        from lib.Dependencies import check_pip_dependencies

        final_status = check_pip_dependencies(pip_deps)

        # Verify installation results for required dependencies
        for dep in pip_deps:
            if not dep.optional:
                # Check if dependency is satisfied
                is_satisfied = final_status.get(dep.name, False)
                was_installed = result.get(dep.name, False)

                assert is_satisfied or was_installed, (
                    f"Required dependency {dep.name} is not satisfied. "
                    f"Final status: {is_satisfied}, Installation result: {was_installed}"
                )

        # Verify cryptography specifically since it's critical for marketplace security
        try:
            import cryptography

            assert hasattr(
                cryptography, "__version__"
            ), "cryptography import successful but missing expected attributes"
        except ImportError:
            pytest.fail("cryptography library not available after installation")

    @pytest.fixture
    def mock_env_configured(self):
        """Mock environment with marketplace configured"""
        with patch("extensions.auth_marketplace.EXT_Auth_Marketplace.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "redis://localhost:6379/2",
                "STRIPE_SECRET_KEY": "sk_test_123456789",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_env_no_redis(self):
        """Mock environment without Redis configured"""
        with patch("extensions.auth_marketplace.EXT_Auth_Marketplace.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "",
                "STRIPE_SECRET_KEY": "sk_test_123456789",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_cryptography_available(self):
        """Mock cryptography library availability"""
        mock_crypto = MagicMock()

        with patch.dict("sys.modules", {"cryptography": mock_crypto}):
            yield mock_crypto

    @pytest.fixture
    def mock_redis_available(self):
        """Mock Redis library availability"""
        mock_redis = MagicMock()
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.set.return_value = True
        mock_client.get.return_value = None
        mock_client.delete.return_value = True
        mock_redis.from_url.return_value = mock_client

        with patch.dict("sys.modules", {"redis": mock_redis}):
            yield mock_redis

    @pytest.fixture
    def mock_stripe_available(self):
        """Mock Stripe library availability"""
        mock_stripe = MagicMock()
        mock_intent = MagicMock()
        mock_intent.id = "pi_test_123"
        mock_intent.client_secret = "pi_test_123_secret"

        mock_payment_intent = MagicMock()
        mock_payment_intent.create.return_value = mock_intent

        mock_stripe.PaymentIntent = mock_payment_intent
        mock_stripe.api_key = None

        with patch.dict("sys.modules", {"stripe": mock_stripe}):
            yield mock_stripe

    @pytest.fixture
    def mock_requests_available(self):
        """Mock requests library availability"""
        mock_requests = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "verified"}
        mock_response.status_code = 200
        mock_requests.post.return_value = mock_response

        with patch.dict("sys.modules", {"requests": mock_requests}):
            yield mock_requests

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_extension_metadata(self, extension):
        """Test extension metadata and basic attributes"""
        assert extension.name == "auth_marketplace"
        assert extension.version == "1.0.0"
        assert "Marketplace authentication extension" in extension.description
        assert hasattr(extension, "capabilities")
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "db_tables")

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_dependencies_structure(self, extension):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        assert len(extension.__class__.ext_dependencies) == 3
        ext_deps = {dep.name: dep for dep in extension.__class__.ext_dependencies}

        assert "core" in ext_deps
        assert not ext_deps["core"].optional

        assert "auth" in ext_deps
        assert not ext_deps["auth"].optional

        assert "auth_api_keys" in ext_deps
        assert ext_deps["auth_api_keys"].optional

        # Check pip dependencies
        assert len(extension.__class__.pip_dependencies) == 4
        pip_deps = {dep.name: dep for dep in extension.__class__.pip_dependencies}

        assert "stripe" in pip_deps
        assert pip_deps["stripe"].optional

        assert "cryptography" in pip_deps
        assert not pip_deps["cryptography"].optional

        assert "requests" in pip_deps
        assert not pip_deps["requests"].optional

        assert "redis" in pip_deps
        assert pip_deps["redis"].optional

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_db_tables_structure(self, extension):
        """Test that database tables are properly defined"""
        assert len(extension.__class__.db_tables) == 3
        table_names = [table.__name__ for table in extension.__class__.db_tables]
        assert "MarketplaceVendor" in table_names
        assert "MarketplaceListing" in table_names
        assert "MarketplaceTransaction" in table_names

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_marketplace_constants_structure(self, extension):
        """Test that marketplace constants are properly defined"""
        verification_levels = extension.get_verification_levels()
        assert isinstance(verification_levels, dict)
        assert "basic" in verification_levels
        assert "enhanced" in verification_levels
        assert "premium" in verification_levels

        transaction_statuses = extension.get_transaction_statuses()
        assert isinstance(transaction_statuses, dict)
        assert "pending" in transaction_statuses
        assert "completed" in transaction_statuses
        assert "failed" in transaction_statuses

        marketplace_roles = extension.get_marketplace_roles()
        assert isinstance(marketplace_roles, dict)
        assert "buyer" in marketplace_roles
        assert "vendor" in marketplace_roles
        assert "admin" in marketplace_roles

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Marketplace(
                enable_vendor_verification=False,
                require_kyc_for_vendors=False,
                enable_fraud_detection=False,
                max_transaction_amount=5000.0,
                enable_escrow=False,
                min_vendor_reputation=4.0,
                enable_payment_integration=False,
            )

            assert extension.enable_vendor_verification is False
            assert extension.require_kyc_for_vendors is False
            assert extension.enable_fraud_detection is False
            assert extension.max_transaction_amount == 5000.0
            assert extension.enable_escrow is False
            assert extension.min_vendor_reputation == 4.0
            assert extension.enable_payment_integration is False

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_on_initialize_success_with_redis(
        self, mock_redis_available, mock_stripe_available
    ):
        """Test successful extension initialization with Redis and Stripe"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_Marketplace, "_register_marketplace_hooks"
            ) as mock_hooks:
                extension = EXT_Auth_Marketplace(
                    enable_fraud_detection=True,
                    enable_payment_integration=True,
                    stripe_api_key="sk_test_123",
                )
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_on_initialize_success_without_redis(self):
        """Test successful extension initialization without Redis"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_Marketplace, "_register_marketplace_hooks"
            ) as mock_hooks:
                extension = EXT_Auth_Marketplace(enable_fraud_detection=False)
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_on_initialize_failure(self):
        """Test extension initialization failure handling"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_Marketplace,
                "_initialize_redis",
                side_effect=Exception("Test error"),
            ):
                extension = EXT_Auth_Marketplace()
                result = extension.on_initialize()

                assert result is False

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_initialize_redis_success(self, mock_redis_available, mock_env_configured):
        """Test successful Redis initialization"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Marketplace()
            extension._initialize_redis()

            assert extension.redis_client is not None

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_initialize_payment_processing_success(
        self, mock_stripe_available, mock_env_configured
    ):
        """Test successful payment processing initialization"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Marketplace(stripe_api_key="sk_test_123")
            extension._initialize_payment_processing()

            # Should set Stripe API key without errors

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_get_capabilities(self, extension):
        """Test getting extension capabilities"""
        capabilities = extension.get_capabilities()

        assert isinstance(capabilities, set)
        for expected_capability in self.expected_capabilities:
            assert expected_capability in capabilities

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_register_capability(self, extension):
        """Test registering new capability"""
        new_capability = "test_marketplace_capability"
        extension.register_capability(new_capability)

        assert new_capability in extension.capabilities
        assert new_capability in extension.get_registered_capabilities()

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_verify_vendor_success(self, extension):
        """Test successful vendor verification"""
        with patch.object(extension, "_perform_verification") as mock_verify:
            mock_verify.return_value = {"success": True, "score": 85}

            result = await extension.verify_vendor(
                vendor_id="vendor123",
                verification_level="basic",
                verification_data={"business_email": "test@business.com"},
            )

            assert result["success"] is True
            assert "verification" in result
            assert result["vendor_id"] == "vendor123"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_verify_vendor_already_verified(self, extension):
        """Test vendor verification when already verified"""
        # Setup existing verification
        extension.verified_vendors["vendor123"] = {
            "vendor_id": "vendor123",
            "verification_level": "basic",
            "status": "verified",
        }

        result = await extension.verify_vendor(
            vendor_id="vendor123",
            verification_level="basic",
        )

        assert result["success"] is True
        assert "already verified" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_verify_vendor_verification_disabled(self, extension):
        """Test vendor verification when disabled"""
        extension.enable_vendor_verification = False

        result = await extension.verify_vendor(
            vendor_id="vendor123",
            verification_level="basic",
        )

        assert result["success"] is False
        assert "not enabled" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_authenticate_transaction_success(self, extension):
        """Test successful transaction authentication"""
        # Setup verified vendor
        extension.verified_vendors["vendor123"] = {
            "vendor_id": "vendor123",
            "verification_level": "basic",
            "status": "verified",
        }

        # Setup vendor reputation
        extension.reputation_cache["vendor_ratings"] = {
            "vendor123": {"average_rating": 4.5}
        }

        with patch.object(extension, "_check_transaction_fraud") as mock_fraud:
            mock_fraud.return_value = {"is_suspicious": False}

            result = await extension.authenticate_transaction(
                transaction_data={
                    "buyer_id": "buyer123",
                    "vendor_id": "vendor123",
                    "amount": 100.0,
                    "listing_id": "listing123",
                }
            )

            assert result["success"] is True
            assert "transaction_id" in result
            assert result["transaction"]["status"] == "authorized"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_authenticate_transaction_invalid_data(self, extension):
        """Test transaction authentication with invalid data"""
        result = await extension.authenticate_transaction(
            transaction_data={
                "buyer_id": "buyer123",
                # Missing required fields
            }
        )

        assert result["success"] is False
        assert "Invalid transaction data" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_authenticate_transaction_amount_exceeds_limit(self, extension):
        """Test transaction authentication with amount exceeding limit"""
        extension.max_transaction_amount = 100.0

        result = await extension.authenticate_transaction(
            transaction_data={
                "buyer_id": "buyer123",
                "vendor_id": "vendor123",
                "amount": 200.0,  # Exceeds limit
                "listing_id": "listing123",
            }
        )

        assert result["success"] is False
        assert "exceeds limit" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_authenticate_transaction_vendor_not_verified(self, extension):
        """Test transaction authentication with unverified vendor"""
        result = await extension.authenticate_transaction(
            transaction_data={
                "buyer_id": "buyer123",
                "vendor_id": "unverified_vendor",
                "amount": 100.0,
                "listing_id": "listing123",
            }
        )

        assert result["success"] is False
        assert "not verified" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_authenticate_transaction_fraud_detected(self, extension):
        """Test transaction authentication with fraud detection"""
        # Setup verified vendor
        extension.verified_vendors["vendor123"] = {
            "vendor_id": "vendor123",
            "verification_level": "basic",
            "status": "verified",
        }

        with patch.object(extension, "_check_transaction_fraud") as mock_fraud:
            mock_fraud.return_value = {
                "is_suspicious": True,
                "reason": "High transaction amount",
            }

            result = await extension.authenticate_transaction(
                transaction_data={
                    "buyer_id": "buyer123",
                    "vendor_id": "vendor123",
                    "amount": 100.0,
                    "listing_id": "listing123",
                },
                fraud_check=True,
            )

            assert result["success"] is False
            assert "flagged for fraud" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_process_payment_success(self, extension, mock_stripe_available):
        """Test successful payment processing"""
        # Setup transaction
        transaction_id = "txn_123"
        extension.active_transactions[transaction_id] = {
            "id": transaction_id,
            "buyer_id": "buyer123",
            "vendor_id": "vendor123",
            "amount": 100.0,
            "status": "authorized",
            "escrow_enabled": False,
        }

        with patch.object(extension, "_process_stripe_payment") as mock_stripe:
            mock_stripe.return_value = {"success": True, "payment_id": "pi_test_123"}

            result = await extension.process_payment(
                transaction_id=transaction_id,
                payment_method="stripe",
            )

            assert result["success"] is True
            assert "payment_id" in result
            assert (
                extension.active_transactions[transaction_id]["status"] == "completed"
            )

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_process_payment_disabled(self, extension):
        """Test payment processing when disabled"""
        extension.enable_payment_integration = False

        result = await extension.process_payment(
            transaction_id="txn_123",
            payment_method="stripe",
        )

        assert result["success"] is False
        assert "not enabled" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_process_payment_transaction_not_found(self, extension):
        """Test payment processing with non-existent transaction"""
        result = await extension.process_payment(
            transaction_id="non_existent_txn",
            payment_method="stripe",
        )

        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_process_payment_with_escrow(self, extension, mock_stripe_available):
        """Test payment processing with escrow enabled"""
        # Setup transaction with escrow
        transaction_id = "txn_123"
        extension.active_transactions[transaction_id] = {
            "id": transaction_id,
            "buyer_id": "buyer123",
            "vendor_id": "vendor123",
            "amount": 100.0,
            "status": "authorized",
            "escrow_enabled": True,
        }

        with patch.object(extension, "_process_stripe_payment") as mock_stripe:
            mock_stripe.return_value = {"success": True, "payment_id": "pi_test_123"}

            with patch.object(extension, "_setup_escrow") as mock_escrow:
                mock_escrow.return_value = {"escrow_id": "escrow_123"}

                result = await extension.process_payment(
                    transaction_id=transaction_id,
                    payment_method="stripe",
                )

                assert result["success"] is True
                assert (
                    extension.active_transactions[transaction_id]["status"] == "pending"
                )
                assert "escrow_id" in extension.active_transactions[transaction_id]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_validate_listing_success(self, extension):
        """Test successful listing validation"""
        # Setup verified vendor
        extension.verified_vendors["vendor123"] = {
            "vendor_id": "vendor123",
            "verification_level": "basic",
            "status": "verified",
        }

        with patch.object(extension, "_validate_listing_content") as mock_validate:
            mock_validate.return_value = {"is_valid": True}

            result = await extension.validate_listing(
                listing_data={
                    "title": "Test Product",
                    "description": "A test product",
                    "price": 50.0,
                    "category": "electronics",
                },
                vendor_id="vendor123",
            )

            assert result["success"] is True
            assert "signature" in result
            assert result["vendor_id"] == "vendor123"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_validate_listing_vendor_not_verified(self, extension):
        """Test listing validation with unverified vendor"""
        result = await extension.validate_listing(
            listing_data={
                "title": "Test Product",
                "description": "A test product",
                "price": 50.0,
                "category": "electronics",
            },
            vendor_id="unverified_vendor",
        )

        assert result["success"] is False
        assert "must be verified" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_validate_listing_missing_fields(self, extension):
        """Test listing validation with missing required fields"""
        # Setup verified vendor
        extension.verified_vendors["vendor123"] = {
            "vendor_id": "vendor123",
            "verification_level": "basic",
            "status": "verified",
        }

        result = await extension.validate_listing(
            listing_data={
                "title": "Test Product",
                # Missing description, price, category
            },
            vendor_id="vendor123",
        )

        assert result["success"] is False
        assert "Missing required fields" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_check_marketplace_access_success(self, extension):
        """Test successful marketplace access check"""
        with patch.object(extension, "_get_user_marketplace_roles") as mock_roles:
            mock_roles.return_value = ["buyer", "vendor"]

            with patch.object(
                extension, "_perform_context_access_check"
            ) as mock_context:
                mock_context.return_value = {"allowed": True}

                result = await extension.check_marketplace_access(
                    user_id="user123",
                    requested_role="vendor",
                )

                assert result["success"] is True
                assert result["access_granted"] is True

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_check_marketplace_access_insufficient_role(self, extension):
        """Test marketplace access check with insufficient role"""
        with patch.object(extension, "_get_user_marketplace_roles") as mock_roles:
            mock_roles.return_value = ["buyer"]  # Doesn't have vendor role

            result = await extension.check_marketplace_access(
                user_id="user123",
                requested_role="vendor",
            )

            assert result["success"] is False
            assert "does not have" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_update_reputation_success(self, extension):
        """Test successful reputation update"""
        result = await extension.update_reputation(
            user_id="vendor123",
            user_type="vendor",
            rating=4.5,
            transaction_id="txn_123",
            feedback="Great service!",
        )

        assert result["success"] is True
        assert "reputation" in result
        assert result["reputation"]["average_rating"] == 4.5

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_update_reputation_disabled(self, extension):
        """Test reputation update when disabled"""
        extension.enable_reputation_system = False

        result = await extension.update_reputation(
            user_id="vendor123",
            user_type="vendor",
            rating=4.5,
        )

        assert result["success"] is False
        assert "not enabled" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_update_reputation_invalid_rating(self, extension):
        """Test reputation update with invalid rating"""
        result = await extension.update_reputation(
            user_id="vendor123",
            user_type="vendor",
            rating=6.0,  # Invalid rating (> 5.0)
        )

        assert result["success"] is False
        assert "between 1.0 and 5.0" in result["message"]

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_validate_config_all_libraries_available(self):
        """Test configuration validation when all libraries are available"""
        with patch("lib.Logging.logger"):
            mock_libs = {
                "cryptography": MagicMock(),
                "requests": MagicMock(),
                "stripe": MagicMock(),
                "redis": MagicMock(),
            }

            with patch.dict("sys.modules", mock_libs):
                extension = EXT_Auth_Marketplace(
                    enable_payment_integration=False,
                    enable_fraud_detection=False,
                )
                issues = extension.validate_config()

                assert len(issues) == 0

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_validate_config_missing_cryptography(self):
        """Test configuration validation with missing cryptography"""
        with patch("lib.Logging.logger"):
            with patch.dict("sys.modules", {"cryptography": None}):
                extension = EXT_Auth_Marketplace()
                issues = extension.validate_config()

                assert len(issues) >= 1
                issue_text = " ".join(issues).lower()
                assert "cryptography" in issue_text

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_validate_config_payment_without_stripe_key(self):
        """Test configuration validation for payment without Stripe key"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Marketplace(
                enable_payment_integration=True, stripe_api_key=None
            )
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "stripe api key" in issue_text

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_get_required_permissions(self, extension):
        """Test getting required permissions"""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 9
        assert "marketplace:read" in permissions
        assert "vendors:verify" in permissions
        assert "transactions:process" in permissions

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_on_start_success(self):
        """Test successful extension start"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Marketplace(
                enable_fraud_detection=False, enable_payment_integration=False
            )
            result = extension.on_start()

            assert result is True

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_on_stop_success(self, extension):
        """Test successful extension stop"""
        # Add some test data
        extension.verified_vendors["vendor1"] = {"status": "verified"}
        extension.active_transactions["txn1"] = {"status": "pending"}
        extension.reputation_cache["vendor_ratings"] = {"vendor1": {"rating": 4.5}}

        result = extension.on_stop()

        assert result is True
        assert len(extension.verified_vendors) == 0
        assert len(extension.active_transactions) == 0
        assert len(extension.reputation_cache) == 0

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_has_capability(self, extension):
        """Test capability checking"""
        assert extension.has_capability("vendor_verification") is True
        assert extension.has_capability("transaction_security") is True
        assert extension.has_capability("non_existent_capability") is False

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_get_marketplace_stats(self, extension):
        """Test getting marketplace statistics"""
        # Add some test data
        extension.verified_vendors["vendor1"] = {"status": "verified"}
        extension.active_transactions["txn1"] = {"status": "pending"}
        extension.fraud_alerts["suspicious_transactions"] = ["txn1"]

        stats = extension.get_marketplace_stats()

        assert isinstance(stats, dict)
        assert stats["verified_vendors"] == 1
        assert stats["active_transactions"] == 1
        assert stats["fraud_alerts"] == 1

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_abilities_discovery(self, extension):
        """Test that all expected abilities are discovered"""
        extension.discover_abilities()

        for expected_ability in self.expected_abilities:
            assert expected_ability in extension.abilities
            assert callable(extension.abilities[expected_ability])

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_execute_ability_success(self, extension):
        """Test successful ability execution"""
        # Setup verified vendor for listing validation
        extension.verified_vendors["vendor123"] = {
            "vendor_id": "vendor123",
            "verification_level": "basic",
            "status": "verified",
        }

        result = await extension.execute_ability(
            "validate_listing",
            {
                "listing_data": {
                    "title": "Test Product",
                    "description": "A test product",
                    "price": 50.0,
                    "category": "electronics",
                },
                "vendor_id": "vendor123",
            },
        )

        assert "success" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_execute_ability_not_found(self, extension):
        """Test executing non-existent ability"""
        result = await extension.execute_ability("non_existent_ability")

        assert "not found" in result

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_marketplace_hooks_registration(self):
        """Test that marketplace hooks are properly registered"""
        with patch("lib.Logging.logger"):
            with patch(
                "extensions.auth_marketplace.EXT_Auth_Marketplace.AbstractExtension.bll_hook"
            ) as mock_hook:
                extension = EXT_Auth_Marketplace()
                extension._register_marketplace_hooks()

                # Should register hooks for vendor and transaction management
                assert mock_hook.called

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_lifecycle_methods_integration(self, extension):
        """Test integration of lifecycle methods"""
        # Test startup
        extension.on_startup()

        # Test shutdown
        extension.on_shutdown()

        # These methods should not raise exceptions

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    async def test_marketplace_full_workflow(self, extension, mock_stripe_available):
        """Test complete marketplace workflow"""
        # 1. Verify vendor
        with patch.object(extension, "_perform_verification") as mock_verify:
            mock_verify.return_value = {"success": True, "score": 85}

            verify_result = await extension.verify_vendor(
                vendor_id="vendor123",
                verification_level="basic",
            )
            assert verify_result["success"] is True

        # 2. Validate listing
        with patch.object(extension, "_validate_listing_content") as mock_validate:
            mock_validate.return_value = {"is_valid": True}

            listing_result = await extension.validate_listing(
                listing_data={
                    "title": "Test Product",
                    "description": "A test product",
                    "price": 50.0,
                    "category": "electronics",
                },
                vendor_id="vendor123",
            )
            assert listing_result["success"] is True

        # 3. Authenticate transaction
        with patch.object(extension, "_check_transaction_fraud") as mock_fraud:
            mock_fraud.return_value = {"is_suspicious": False}

            transaction_result = await extension.authenticate_transaction(
                transaction_data={
                    "buyer_id": "buyer123",
                    "vendor_id": "vendor123",
                    "amount": 50.0,
                    "listing_id": "listing123",
                }
            )
            assert transaction_result["success"] is True
            transaction_id = transaction_result["transaction_id"]

        # 4. Process payment
        with patch.object(extension, "_process_stripe_payment") as mock_stripe:
            mock_stripe.return_value = {"success": True, "payment_id": "pi_test_123"}

            payment_result = await extension.process_payment(
                transaction_id=transaction_id,
                payment_method="stripe",
            )
            assert payment_result["success"] is True

        # 5. Update reputation
        reputation_result = await extension.update_reputation(
            user_id="vendor123",
            user_type="vendor",
            rating=5.0,
            transaction_id=transaction_id,
        )
        assert reputation_result["success"] is True

    @pytest.mark.dependency(depends=["auth_marketplace_dependencies"])
    def test_security_signature_generation(self, extension):
        """Test that listing signatures are properly generated and secure"""
        listing_data = {
            "title": "Test Product",
            "description": "A test product",
            "price": 50.0,
            "category": "electronics",
        }

        # Generate multiple signatures to ensure consistency
        signature1 = extension._generate_listing_signature(listing_data, "vendor123")
        signature2 = extension._generate_listing_signature(listing_data, "vendor123")

        # Same input should produce same signature
        assert signature1 == signature2

        # Different vendor should produce different signature
        signature3 = extension._generate_listing_signature(listing_data, "vendor456")
        assert signature1 != signature3

        # Signatures should be hex strings
        assert all(c in "0123456789abcdef" for c in signature1)
