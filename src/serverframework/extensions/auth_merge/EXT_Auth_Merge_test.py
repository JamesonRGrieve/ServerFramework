from typing import List
from unittest.mock import MagicMock, patch

import pytest

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, SkipThisTest
from serverframework.extensions.AbstractEXTTest import AbstractEXTTest
from serverframework.extensions.auth_merge.EXT_Auth_Merge import EXT_Auth_Merge
from serverframework.lib.Dependencies import install_pip_dependencies


class TestEXTAuthMerge(AbstractEXTTest):
    """
    Test suite for EXT_Auth_Merge extension.

    Tests extension initialization, account merging capabilities, abilities, and duplicate
    detection functionality. Focuses on testing duplicate detection, merge workflows,
    conflict resolution, and rollback functionality rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - Account merge capabilities and abilities (duplicate detection, merge processing, rollback)
    - Fuzzy matching and Redis integration
    - Merge request workflow management
    - Conflict resolution and data consolidation
    - Rollback and audit functionality
    - Data integrity and security validation
    """

    # Configure the test class
    extension_class = EXT_Auth_Merge
    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.EXTENSION, CategoryOfTest.INTEGRATION],
        cleanup=True,
    )

    # Expected extension properties
    expected_abilities = [
        "detect_duplicates",
        "create_merge_request",
        "process_merge",
        "rollback_merge",
        "get_merge_preview",
    ]

    expected_capabilities = [
        "duplicate_detection",
        "merge_workflow",
        "conflict_resolution",
        "data_consolidation",
        "merge_verification",
        "merge_audit",
        "rollback_support",
    ]

    # Tests to skip
    _skip_tests: List[SkipThisTest] = []

    @pytest.mark.dependency(name="auth_merge_dependencies")
    def test_install_pip_dependencies(self):
        """
        Install PIP dependencies required by the Auth Merge extension.
        This test must run first and all other Auth Merge tests depend on it.
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

        # Verify difflib specifically since it's critical for conflict detection
        try:
            import difflib

            assert hasattr(
                difflib, "SequenceMatcher"
            ), "difflib import successful but missing expected attributes"
        except ImportError:
            pytest.fail("difflib library not available after installation")

    @pytest.fixture
    def mock_env_configured(self):
        """Mock environment with merge configured"""
        with patch("extensions.auth_merge.EXT_Auth_Merge.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "redis://localhost:6379/3",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

    @pytest.fixture
    def mock_env_no_redis(self):
        """Mock environment without Redis configured"""
        with patch("extensions.auth_merge.EXT_Auth_Merge.env") as mock_env:
            mock_env.side_effect = lambda key, default=None: {
                "REDIS_URL": "",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "%(message)s",
            }.get(key, default)
            yield mock_env

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
    def mock_fuzzywuzzy_available(self):
        """Mock fuzzywuzzy library availability"""
        mock_fuzzywuzzy = MagicMock()
        mock_fuzz = MagicMock()
        mock_fuzz.ratio.return_value = 85
        mock_fuzzywuzzy.fuzz = mock_fuzz

        with patch.dict("sys.modules", {"fuzzywuzzy": mock_fuzzywuzzy}):
            yield mock_fuzzywuzzy

    @pytest.fixture
    def mock_phonenumbers_available(self):
        """Mock phonenumbers library availability"""
        mock_phonenumbers = MagicMock()
        mock_phonenumbers.parse.return_value = MagicMock()
        mock_phonenumbers.format_number.return_value = "+1234567890"

        with patch.dict("sys.modules", {"phonenumbers": mock_phonenumbers}):
            yield mock_phonenumbers

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_extension_metadata(self, extension):
        """Test extension metadata and basic attributes"""
        assert extension.name == "auth_merge"
        assert extension.version == "1.0.0"
        assert "Account Merge authentication extension" in extension.description
        assert hasattr(extension, "capabilities")
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "db_tables")

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_dependencies_structure(self, extension):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        assert len(extension.__class__.ext_dependencies) == 3
        ext_deps = {dep.name: dep for dep in extension.__class__.ext_dependencies}

        assert "core" in ext_deps
        assert not ext_deps["core"].optional

        assert "auth" in ext_deps
        assert not ext_deps["auth"].optional

        assert "email" in ext_deps
        assert ext_deps["email"].optional

        # Check pip dependencies
        assert len(extension.__class__.pip_dependencies) == 4
        pip_deps = {dep.name: dep for dep in extension.__class__.pip_dependencies}

        assert "difflib" in pip_deps
        assert not pip_deps["difflib"].optional

        assert "phonenumbers" in pip_deps
        assert pip_deps["phonenumbers"].optional

        assert "fuzzywuzzy" in pip_deps
        assert pip_deps["fuzzywuzzy"].optional

        assert "redis" in pip_deps
        assert pip_deps["redis"].optional

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_db_tables_structure(self, extension):
        """Test that database tables are properly defined"""
        assert len(extension.__class__.db_tables) == 3
        table_names = [table.__name__ for table in extension.__class__.db_tables]
        assert "MergeRequest" in table_names
        assert "MergeHistory" in table_names
        assert "AccountConflict" in table_names

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_merge_constants_structure(self, extension):
        """Test that merge constants are properly defined"""
        merge_statuses = extension.get_merge_statuses()
        assert isinstance(merge_statuses, dict)
        assert "pending" in merge_statuses
        assert "completed" in merge_statuses
        assert "failed" in merge_statuses

        conflict_strategies = extension.get_conflict_strategies()
        assert isinstance(conflict_strategies, dict)
        assert "keep_primary" in conflict_strategies
        assert "keep_secondary" in conflict_strategies
        assert "merge_both" in conflict_strategies

        field_priorities = extension.get_field_priorities()
        assert isinstance(field_priorities, dict)
        assert "email" in field_priorities
        assert "phone" in field_priorities

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Merge(
                enable_automatic_detection=False,
                enable_fuzzy_matching=False,
                similarity_threshold=0.95,
                require_verification=False,
                enable_merge_preview=False,
                max_conflicts_auto_resolve=10,
                enable_rollback=False,
            )

            assert extension.enable_automatic_detection is False
            assert extension.enable_fuzzy_matching is False
            assert extension.similarity_threshold == 0.95
            assert extension.require_verification is False
            assert extension.enable_merge_preview is False
            assert extension.max_conflicts_auto_resolve == 10
            assert extension.enable_rollback is False

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_on_initialize_success_with_redis(self, mock_redis_available):
        """Test successful extension initialization with Redis"""
        with patch("lib.Logging.logger"):
            with patch.object(EXT_Auth_Merge, "_register_merge_hooks") as mock_hooks:
                extension = EXT_Auth_Merge()
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_on_initialize_success_without_redis(self):
        """Test successful extension initialization without Redis"""
        with patch("lib.Logging.logger"):
            with patch.object(EXT_Auth_Merge, "_register_merge_hooks") as mock_hooks:
                extension = EXT_Auth_Merge()
                result = extension.on_initialize()

                assert result is True
                mock_hooks.assert_called_once()

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_on_initialize_failure(self):
        """Test extension initialization failure handling"""
        with patch("lib.Logging.logger"):
            with patch.object(
                EXT_Auth_Merge,
                "_initialize_redis",
                side_effect=Exception("Test error"),
            ):
                extension = EXT_Auth_Merge()
                result = extension.on_initialize()

                assert result is False

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_initialize_redis_success(self, mock_redis_available, mock_env_configured):
        """Test successful Redis initialization"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Merge()
            extension._initialize_redis()

            assert extension.redis_client is not None

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_get_capabilities(self, extension):
        """Test getting extension capabilities"""
        capabilities = extension.get_capabilities()

        assert isinstance(capabilities, set)
        for expected_capability in self.expected_capabilities:
            assert expected_capability in capabilities

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_register_capability(self, extension):
        """Test registering new capability"""
        new_capability = "test_merge_capability"
        extension.register_capability(new_capability)

        assert new_capability in extension.capabilities
        assert new_capability in extension.get_registered_capabilities()

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_detect_duplicates_success_email_match(self, extension):
        """Test successful duplicate detection with email match"""
        with patch.object(extension, "_find_email_matches") as mock_email:
            mock_email.return_value = [
                {
                    "user_id": "user456",
                    "email": "test@example.com",
                    "name": "Test User",
                }
            ]

            with patch.object(extension, "_deduplicate_and_score") as mock_score:
                mock_score.return_value = [
                    {
                        "user_id": "user456",
                        "email": "test@example.com",
                        "similarity_score": 0.95,
                    }
                ]

                result = await extension.detect_duplicates(
                    user_data={
                        "email": "test@example.com",
                        "name": "Test User",
                    }
                )

                assert result["success"] is True
                assert result["total_found"] == 1
                assert len(result["duplicates"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_detect_duplicates_no_email_or_phone(self, extension):
        """Test duplicate detection without email or phone"""
        result = await extension.detect_duplicates(
            user_data={
                "name": "Test User",
                # Missing email and phone
            }
        )

        assert result["success"] is False
        assert "Email or phone required" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_detect_duplicates_with_fuzzy_matching(
        self, extension, mock_fuzzywuzzy_available
    ):
        """Test duplicate detection with fuzzy matching enabled"""
        extension.enable_fuzzy_matching = True

        with patch.object(extension, "_find_email_matches") as mock_email:
            mock_email.return_value = []

        with patch.object(extension, "_find_phone_matches") as mock_phone:
            mock_phone.return_value = []

        with patch.object(extension, "_find_fuzzy_name_matches") as mock_fuzzy:
            mock_fuzzy.return_value = [
                {
                    "user_id": "user789",
                    "name": "Jon Doe",  # Similar to "John Doe"
                    "email": "jon@example.com",
                }
            ]

        with patch.object(extension, "_deduplicate_and_score") as mock_score:
            mock_score.return_value = [
                {
                    "user_id": "user789",
                    "name": "Jon Doe",
                    "similarity_score": 0.90,
                }
            ]

            result = await extension.detect_duplicates(
                user_data={
                    "email": "john@example.com",
                    "name": "John Doe",
                },
                include_fuzzy=True,
            )

            assert result["success"] is True
            assert result["total_found"] == 1

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_create_merge_request_success(self, extension):
        """Test successful merge request creation"""
        with patch.object(extension, "_check_existing_merge_request") as mock_existing:
            mock_existing.return_value = None

        with patch.object(extension, "_analyze_merge_conflicts") as mock_conflicts:
            mock_conflicts.return_value = [
                {
                    "field": "email",
                    "primary_value": "user1@example.com",
                    "secondary_value": "user2@example.com",
                    "conflict_type": "different_values",
                    "priority": "high",
                }
            ]

        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "merge-request-123"

            result = await extension.create_merge_request(
                primary_user_id="user123",
                secondary_user_id="user456",
            )

            assert result["success"] is True
            assert "merge_request_id" in result
            assert result["merge_request"]["status"] == "requires_verification"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_create_merge_request_same_user(self, extension):
        """Test merge request creation with same user ID"""
        result = await extension.create_merge_request(
            primary_user_id="user123",
            secondary_user_id="user123",
        )

        assert result["success"] is False
        assert "Cannot merge account with itself" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_create_merge_request_existing_request(self, extension):
        """Test merge request creation when request already exists"""
        existing_request = {
            "id": "existing-123",
            "primary_user_id": "user123",
            "secondary_user_id": "user456",
        }

        with patch.object(extension, "_check_existing_merge_request") as mock_existing:
            mock_existing.return_value = existing_request

            result = await extension.create_merge_request(
                primary_user_id="user123",
                secondary_user_id="user456",
            )

            assert result["success"] is False
            assert "already exists" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_create_merge_request_auto_resolvable(self, extension):
        """Test merge request creation that is auto-resolvable"""
        extension.max_conflicts_auto_resolve = 5
        extension.require_verification = False

        with patch.object(extension, "_check_existing_merge_request") as mock_existing:
            mock_existing.return_value = None

        with patch.object(extension, "_analyze_merge_conflicts") as mock_conflicts:
            mock_conflicts.return_value = [
                # Only 2 conflicts (under the limit)
                {"field": "phone", "conflict_type": "different_values"},
                {"field": "address", "conflict_type": "different_values"},
            ]

        result = await extension.create_merge_request(
            primary_user_id="user123",
            secondary_user_id="user456",
        )

        assert result["success"] is True
        assert result["auto_resolvable"] is True
        assert result["merge_request"]["status"] == "ready_for_processing"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_process_merge_success(self, extension):
        """Test successful merge processing"""
        # Setup merge request
        merge_request_id = "merge-123"
        extension.pending_merges[merge_request_id] = {
            "id": merge_request_id,
            "primary_user_id": "user123",
            "secondary_user_id": "user456",
            "status": "ready_for_processing",
            "conflicts": [],
            "requires_verification": False,
        }

        with patch.object(extension, "_resolve_all_conflicts") as mock_resolve:
            mock_resolve.return_value = {"success": True, "resolved_conflicts": []}

        with patch.object(extension, "_perform_account_merge") as mock_merge:
            mock_merge.return_value = {
                "success": True,
                "merged_user_id": "user123",
                "summary": {"data_fields_merged": 5},
            }

        with patch.object(extension, "_create_merge_history") as mock_history:
            mock_history.return_value = "history-123"

        result = await extension.process_merge(merge_request_id=merge_request_id)

        assert result["success"] is True
        assert result["merged_user_id"] == "user123"
        assert extension.pending_merges[merge_request_id]["status"] == "completed"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_process_merge_not_found(self, extension):
        """Test merge processing with non-existent request"""
        result = await extension.process_merge(merge_request_id="non-existent")

        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_process_merge_requires_verification(self, extension):
        """Test merge processing that requires verification"""
        # Setup merge request requiring verification
        merge_request_id = "merge-123"
        extension.pending_merges[merge_request_id] = {
            "id": merge_request_id,
            "status": "requires_verification",
            "requires_verification": True,
        }

        result = await extension.process_merge(
            merge_request_id=merge_request_id,
            # No verification code provided
        )

        assert result["success"] is False
        assert "Verification code required" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_process_merge_with_verification(self, extension):
        """Test merge processing with verification code"""
        # Setup merge request
        merge_request_id = "merge-123"
        extension.pending_merges[merge_request_id] = {
            "id": merge_request_id,
            "status": "requires_verification",
            "requires_verification": True,
            "conflicts": [],
        }

        with patch.object(extension, "_verify_merge_request") as mock_verify:
            mock_verify.return_value = {"valid": True}

        with patch.object(extension, "_resolve_all_conflicts") as mock_resolve:
            mock_resolve.return_value = {"success": True}

        with patch.object(extension, "_perform_account_merge") as mock_merge:
            mock_merge.return_value = {"success": True, "merged_user_id": "user123"}

        with patch.object(extension, "_create_merge_history") as mock_history:
            mock_history.return_value = "history-123"

        result = await extension.process_merge(
            merge_request_id=merge_request_id,
            verification_code="123456",
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_process_merge_conflict_resolution_failure(self, extension):
        """Test merge processing when conflict resolution fails"""
        # Setup merge request
        merge_request_id = "merge-123"
        extension.pending_merges[merge_request_id] = {
            "id": merge_request_id,
            "status": "ready_for_processing",
            "conflicts": [],
            "requires_verification": False,
        }

        with patch.object(extension, "_resolve_all_conflicts") as mock_resolve:
            mock_resolve.return_value = {
                "success": False,
                "message": "Conflict resolution failed",
            }

        result = await extension.process_merge(merge_request_id=merge_request_id)

        assert result["success"] is False
        assert "Conflict resolution failed" in result["message"]
        assert extension.pending_merges[merge_request_id]["status"] == "failed"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_rollback_merge_success(self, extension):
        """Test successful merge rollback"""
        with patch.object(extension, "_get_merge_history") as mock_history:
            mock_history.return_value = {
                "id": "history-123",
                "completed_at": "2023-01-01T00:00:00",
                "secondary_user_id": "user456",
            }

        with patch.object(extension, "_perform_merge_rollback") as mock_rollback:
            mock_rollback.return_value = {
                "success": True,
                "summary": {"accounts_restored": 1},
                "restored_accounts": ["user456"],
            }

        result = await extension.rollback_merge(
            merge_history_id="history-123",
            reason="User requested rollback",
        )

        assert result["success"] is True
        assert "restored_accounts" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_rollback_merge_disabled(self, extension):
        """Test merge rollback when disabled"""
        extension.enable_rollback = False

        result = await extension.rollback_merge(
            merge_history_id="history-123",
        )

        assert result["success"] is False
        assert "disabled" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_rollback_merge_history_not_found(self, extension):
        """Test merge rollback with non-existent history"""
        with patch.object(extension, "_get_merge_history") as mock_history:
            mock_history.return_value = None

        result = await extension.rollback_merge(
            merge_history_id="non-existent",
        )

        assert result["success"] is False
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_rollback_merge_expired(self, extension):
        """Test merge rollback when period has expired"""
        from datetime import datetime, timedelta

        # Set a short retention period
        extension.rollback_retention_days = 7

        expired_date = (datetime.utcnow() - timedelta(days=10)).isoformat()

        with patch.object(extension, "_get_merge_history") as mock_history:
            mock_history.return_value = {
                "id": "history-123",
                "completed_at": expired_date,
            }

        result = await extension.rollback_merge(
            merge_history_id="history-123",
        )

        assert result["success"] is False
        assert "expired" in result["message"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_get_merge_preview_success(self, extension):
        """Test successful merge preview generation"""
        with patch.object(extension, "_analyze_merge_conflicts") as mock_conflicts:
            mock_conflicts.return_value = [
                {"field": "email", "conflict_type": "different_values"},
                {"field": "phone", "conflict_type": "different_values"},
            ]

        with patch.object(extension, "_generate_merge_preview") as mock_preview:
            mock_preview.return_value = {
                "merged_fields": {"name": "John Doe"},
                "conflicts_resolved": 2,
                "data_changes": ["email updated", "phone updated"],
            }

        result = await extension.get_merge_preview(
            primary_user_id="user123",
            secondary_user_id="user456",
        )

        assert result["success"] is True
        assert "preview" in result
        assert result["total_conflicts"] == 2

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_calculate_similarity_score(self, extension):
        """Test similarity score calculation"""
        candidate = {
            "email": "test@example.com",
            "phone": "+1234567890",
            "name": "John Doe",
        }

        user_data = {
            "email": "test@example.com",  # Exact match
            "phone": "+1234567890",  # Exact match
            "name": "John Doe",  # Exact match
        }

        score = extension._calculate_similarity_score(candidate, user_data)

        # Should be high score for exact matches
        assert score > 0.8

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_calculate_similarity_score_partial_match(self, extension):
        """Test similarity score calculation with partial matches"""
        candidate = {
            "email": "test@example.com",
            "phone": "+1234567890",
            "name": "Jane Doe",  # Different name
        }

        user_data = {
            "email": "test@example.com",  # Same email
            "phone": "+0987654321",  # Different phone
            "name": "John Doe",  # Different name
        }

        score = extension._calculate_similarity_score(candidate, user_data)

        # Should be moderate score for partial match
        assert 0.2 < score < 0.8

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_conflict_resolvers_email(self, extension):
        """Test email conflict resolution"""
        conflict = {
            "field": "email",
            "primary_value": "primary@example.com",
            "secondary_value": "secondary@example.com",
        }

        # Test keep_primary strategy
        result = await extension._resolve_email_conflict(conflict, "keep_primary")
        assert result["resolved_value"] == "primary@example.com"

        # Test keep_secondary strategy
        result = await extension._resolve_email_conflict(conflict, "keep_secondary")
        assert result["resolved_value"] == "secondary@example.com"

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_conflict_resolvers_name_merge_both(self, extension):
        """Test name conflict resolution with merge strategy"""
        conflict = {
            "field": "name",
            "primary_value": "John Smith",
            "secondary_value": "J. Smith",
        }

        result = await extension._resolve_name_conflict(conflict, "merge_both")
        assert "John Smith" in result["resolved_value"]
        assert "J. Smith" in result["resolved_value"]

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_conflict_resolvers_preferences_merge(self, extension):
        """Test preferences conflict resolution with merge strategy"""
        conflict = {
            "field": "preferences",
            "primary_value": '{"theme": "dark", "lang": "en"}',
            "secondary_value": '{"notifications": true, "lang": "es"}',
        }

        result = await extension._resolve_preferences_conflict(conflict, "merge_both")

        # Should merge both preference objects
        import json

        resolved_prefs = json.loads(result["resolved_value"])
        assert "theme" in resolved_prefs  # From primary
        assert "notifications" in resolved_prefs  # From secondary
        assert resolved_prefs["lang"] == "en"  # Primary takes precedence

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_validate_config_all_libraries_available(self):
        """Test configuration validation when all libraries are available"""
        with patch("lib.Logging.logger"):
            mock_libs = {
                "fuzzywuzzy": MagicMock(),
                "redis": MagicMock(),
                "phonenumbers": MagicMock(),
            }

            with patch.dict("sys.modules", mock_libs):
                extension = EXT_Auth_Merge(enable_fuzzy_matching=False)
                issues = extension.validate_config()

                assert len(issues) == 0

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_validate_config_invalid_similarity_threshold(self):
        """Test configuration validation with invalid similarity threshold"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Merge(similarity_threshold=1.5)  # Invalid (> 1.0)
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "similarity threshold" in issue_text

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_validate_config_fuzzy_matching_without_library(self):
        """Test configuration validation for fuzzy matching without library"""
        with patch("lib.Logging.logger"):
            with patch.dict("sys.modules", {"fuzzywuzzy": None}):
                extension = EXT_Auth_Merge(enable_fuzzy_matching=True)
                issues = extension.validate_config()

                assert len(issues) >= 1
                issue_text = " ".join(issues).lower()
                assert "fuzzywuzzy" in issue_text

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_get_required_permissions(self, extension):
        """Test getting required permissions"""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 8
        assert "merge:detect" in permissions
        assert "merge:create" in permissions
        assert "merge:process" in permissions
        assert "merge:rollback" in permissions

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_on_start_success(self):
        """Test successful extension start"""
        with patch("lib.Logging.logger"):
            extension = EXT_Auth_Merge()
            result = extension.on_start()

            assert result is True

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_on_stop_success(self, extension):
        """Test successful extension stop"""
        # Add some test data
        extension.pending_merges["merge1"] = {"status": "pending"}
        extension.merge_cache["cache1"] = {"data": "test"}
        extension.duplicate_candidates["dup1"] = {"score": 0.9}

        result = extension.on_stop()

        assert result is True
        assert len(extension.pending_merges) == 0
        assert len(extension.merge_cache) == 0
        assert len(extension.duplicate_candidates) == 0

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_has_capability(self, extension):
        """Test capability checking"""
        assert extension.has_capability("duplicate_detection") is True
        assert extension.has_capability("merge_workflow") is True
        assert extension.has_capability("non_existent_capability") is False

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_get_merge_stats(self, extension):
        """Test getting merge statistics"""
        # Add some test data
        extension.pending_merges["merge1"] = {"status": "pending"}
        extension.duplicate_candidates["dup1"] = {"score": 0.9}

        stats = extension.get_merge_stats()

        assert isinstance(stats, dict)
        assert stats["pending_merges"] == 1
        assert stats["duplicate_candidates"] == 1
        assert (
            stats["automatic_detection_enabled"] == extension.enable_automatic_detection
        )
        assert stats["fuzzy_matching_enabled"] == extension.enable_fuzzy_matching

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_abilities_discovery(self, extension):
        """Test that all expected abilities are discovered"""
        extension.discover_abilities()

        for expected_ability in self.expected_abilities:
            assert expected_ability in extension.abilities
            assert callable(extension.abilities[expected_ability])

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_execute_ability_success(self, extension):
        """Test successful ability execution"""
        with patch.object(extension, "_find_email_matches") as mock_email:
            mock_email.return_value = []

        with patch.object(extension, "_deduplicate_and_score") as mock_score:
            mock_score.return_value = []

        result = await extension.execute_ability(
            "detect_duplicates",
            {
                "user_data": {
                    "email": "test@example.com",
                    "name": "Test User",
                },
            },
        )

        assert "success" in result

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_execute_ability_not_found(self, extension):
        """Test executing non-existent ability"""
        result = await extension.execute_ability("non_existent_ability")

        assert "not found" in result

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_merge_hooks_registration(self):
        """Test that merge hooks are properly registered"""
        with patch("lib.Logging.logger"):
            with patch(
                "extensions.auth_merge.EXT_Auth_Merge.AbstractExtension.bll_hook"
            ) as mock_hook:
                extension = EXT_Auth_Merge()
                extension._register_merge_hooks()

                # Should register hooks for user creation and updates
                assert mock_hook.called

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_lifecycle_methods_integration(self, extension):
        """Test integration of lifecycle methods"""
        # Test startup
        extension.on_startup()

        # Test shutdown
        extension.on_shutdown()

        # These methods should not raise exceptions

    @pytest.mark.asyncio
    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    async def test_merge_full_workflow(self, extension):
        """Test complete merge workflow"""
        # 1. Detect duplicates
        with patch.object(extension, "_find_email_matches") as mock_email:
            mock_email.return_value = [
                {"user_id": "user456", "email": "test@example.com"}
            ]

        with patch.object(extension, "_deduplicate_and_score") as mock_score:
            mock_score.return_value = [{"user_id": "user456", "similarity_score": 0.95}]

        duplicate_result = await extension.detect_duplicates(
            user_data={"email": "test@example.com", "name": "Test User"}
        )
        assert duplicate_result["success"] is True

        # 2. Create merge request
        with patch.object(extension, "_check_existing_merge_request") as mock_existing:
            mock_existing.return_value = None

        with patch.object(extension, "_analyze_merge_conflicts") as mock_conflicts:
            mock_conflicts.return_value = []

        merge_request_result = await extension.create_merge_request(
            primary_user_id="user123",
            secondary_user_id="user456",
        )
        assert merge_request_result["success"] is True
        merge_request_id = merge_request_result["merge_request_id"]

        # 3. Get merge preview
        with patch.object(extension, "_generate_merge_preview") as mock_preview:
            mock_preview.return_value = {"conflicts_resolved": 0}

        preview_result = await extension.get_merge_preview(
            primary_user_id="user123",
            secondary_user_id="user456",
        )
        assert preview_result["success"] is True

        # 4. Process merge
        with patch.object(extension, "_resolve_all_conflicts") as mock_resolve:
            mock_resolve.return_value = {"success": True}

        with patch.object(extension, "_perform_account_merge") as mock_merge:
            mock_merge.return_value = {"success": True, "merged_user_id": "user123"}

        with patch.object(extension, "_create_merge_history") as mock_history:
            mock_history.return_value = "history-123"

        process_result = await extension.process_merge(
            merge_request_id=merge_request_id,
            force=True,  # Skip verification for test
        )
        assert process_result["success"] is True

    @pytest.mark.dependency(depends=["auth_merge_dependencies"])
    def test_data_integrity_deduplication(self, extension):
        """Test that duplicate removal and scoring maintains data integrity"""
        duplicates = [
            {"user_id": "user1", "email": "test@example.com"},
            {"user_id": "user1", "email": "test@example.com"},  # Duplicate
            {"user_id": "user2", "email": "test2@example.com"},
        ]

        user_data = {"email": "test@example.com", "name": "Test User"}

        result = extension._deduplicate_and_score(duplicates, user_data)

        # Should have only 2 unique users
        assert len(result) == 2
        user_ids = [item["user_id"] for item in result]
        assert "user1" in user_ids
        assert "user2" in user_ids

        # All results should have similarity scores
        for item in result:
            assert "similarity_score" in item
            assert 0.0 <= item["similarity_score"] <= 1.0
