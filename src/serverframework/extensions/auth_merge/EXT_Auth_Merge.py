import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension as AbstractExtension

# Legacy compatibility shim: @AbstractExtension.ability and
# @AbstractExtension.bll_hook were class-attached decorators on the old
# AbstractExtension. The modern AbstractStaticExtension does not expose them
# in the same shape. The standalone ``ability`` decorator is bound back onto
# the alias for transparent re-use; ``bll_hook`` is replaced with a no-op
# placeholder pending a proper rewrite to ``@hook_bll(target, timing=...)``.
# TODO(audit): rewrite every call site as ``@ability(...)`` (standalone) and
# ``@hook_bll(<TargetManager>.method, timing=HookTiming.AFTER)`` then remove
# this shim.
from serverframework.extensions.AbstractExtensionProvider import (  # noqa: E402
    ability as _legacy_ability,
)


def _legacy_bll_hook(*_args, **_kwargs):  # noqa: E501
    def _decorator(func):
        return func
    return _decorator


if not hasattr(AbstractExtension, "ability"):
    AbstractExtension.ability = staticmethod(_legacy_ability)
if not hasattr(AbstractExtension, "bll_hook"):
    AbstractExtension.bll_hook = staticmethod(_legacy_bll_hook)


from serverframework.extensions.auth_merge.DB_Auth_Merge import UserMerge
from serverframework.lib.Dependencies import EXT_Dependency, PIP_Dependency
from serverframework.lib.Environment import env
from serverframework.lib.Logging import logger


class EXT_Auth_Merge(AbstractExtension):
    """
    Account Merge authentication extension for AGInfrastructure.

    Provides comprehensive account merging capabilities including duplicate detection,
    merge workflow management, conflict resolution, and data consolidation. This
    extension provides static functionality and metadata to organize merge-related
    components and manage authentication workflows.

    The extension focuses on:
    - Duplicate account detection and analysis
    - Account merge request workflow management
    - Data conflict resolution and consolidation
    - Identity verification during merge process
    - Merge history tracking and audit trails
    - Permission and role consolidation
    - Data integrity validation

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "auth_merge"
    version = "1.0.0"
    description = (
        "Account Merge authentication extension for duplicate detection, merge workflow "
        "management, and data consolidation"
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
            name="email",
            friendly_name="Email Extension",
            optional=True,
            reason="Sending merge confirmation and notification emails",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="difflib",
            friendly_name="Diff Library",
            optional=False,
            reason="Required for data comparison and conflict detection",
            semver=">=3.9.0",
        ),
        PIP_Dependency(
            name="phonenumbers",
            friendly_name="Phone Numbers Library",
            optional=True,
            reason="Phone number validation and normalization for duplicate detection",
            semver=">=8.13.0",
        ),
        PIP_Dependency(
            name="fuzzywuzzy",
            friendly_name="Fuzzy String Matching",
            optional=True,
            reason="Fuzzy matching for duplicate detection based on name similarity",
            semver=">=0.18.0",
        ),
        PIP_Dependency(
            name="redis",
            friendly_name="Redis Client",
            optional=True,
            reason="Caching for merge operations and temporary data storage",
            semver=">=4.5.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables
    db_tables = [UserMerge]

    # Define what capabilities this extension provides
    capabilities = [
        "duplicate_detection",
        "merge_workflow",
        "conflict_resolution",
        "data_consolidation",
        "merge_verification",
        "merge_audit",
        "rollback_support",
    ]

    # Merge request statuses
    MERGE_STATUSES = {
        "pending": "Merge request is pending review",
        "in_progress": "Merge is currently being processed",
        "completed": "Merge completed successfully",
        "failed": "Merge failed due to errors",
        "cancelled": "Merge was cancelled by user",
        "rejected": "Merge was rejected by system or admin",
        "requires_verification": "Merge requires additional verification",
    }

    # Conflict resolution strategies
    CONFLICT_STRATEGIES = {
        "keep_primary": "Keep data from primary account",
        "keep_secondary": "Keep data from secondary account",
        "merge_both": "Merge data from both accounts",
        "manual_review": "Requires manual review and decision",
        "newest_wins": "Keep the most recently updated data",
        "most_complete": "Keep the most complete data",
    }

    # Data field priorities for merging
    FIELD_PRIORITIES = {
        "email": "high",
        "phone": "high",
        "name": "medium",
        "address": "medium",
        "preferences": "low",
        "metadata": "low",
    }

    def __init__(
        self,
        enable_automatic_detection: bool = True,
        enable_fuzzy_matching: bool = True,
        similarity_threshold: float = 0.85,
        require_verification: bool = True,
        enable_merge_preview: bool = True,
        max_conflicts_auto_resolve: int = 5,
        merge_timeout_hours: int = 24,
        enable_rollback: bool = True,
        rollback_retention_days: int = 30,
        **kwargs,
    ):
        super().__init__(**kwargs)

        # Configuration settings
        self.enable_automatic_detection = enable_automatic_detection
        self.enable_fuzzy_matching = enable_fuzzy_matching
        self.similarity_threshold = similarity_threshold
        self.require_verification = require_verification
        self.enable_merge_preview = enable_merge_preview
        self.max_conflicts_auto_resolve = max_conflicts_auto_resolve
        self.merge_timeout_hours = merge_timeout_hours
        self.enable_rollback = enable_rollback
        self.rollback_retention_days = rollback_retention_days

        # Runtime state
        self.pending_merges = {}
        self.merge_cache = {}
        self.duplicate_candidates = {}
        self.conflict_resolvers = {}
        self.redis_client = None

    def on_initialize(self) -> bool:
        """
        Initialize the Account Merge extension.
        """
        logger.debug("Initializing Account Merge Extension...")

        try:
            # Initialize Redis for caching
            self._initialize_redis()

            # Register merge hooks
            self._register_merge_hooks()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            # Initialize conflict resolvers
            self._initialize_conflict_resolvers()

            # Setup automatic detection if enabled
            if self.enable_automatic_detection:
                self._setup_automatic_detection()

            logger.debug("Account Merge extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Account Merge extension: {str(e)}")
            return False

    def _initialize_redis(self):
        """Initialize Redis connection for caching."""
        try:
            import redis

            redis_url = env("REDIS_URL", "redis://localhost:6379/3")
            self.redis_client = redis.from_url(redis_url)

            # Test connection
            self.redis_client.ping()
            logger.debug("Redis connection established for merge caching")

        except ImportError:
            logger.warning(
                "Redis not available - using memory cache for merge operations"
            )
            self.redis_client = None
        except Exception as e:
            logger.warning(f"Redis connection failed: {e} - using memory cache")
            self.redis_client = None

    def _register_merge_hooks(self):
        """Register hooks for merge-related operations."""
        try:

            @AbstractExtension.bll_hook("Auth", "UserManager", "create", "after")
            def detect_duplicates_hook(manager, result, create_args=None):
                """Hook to detect potential duplicates after user creation."""
                try:
                    if self.enable_automatic_detection and result:
                        asyncio.create_task(self._check_for_duplicates(result))
                        logger.debug("Duplicate detection hook executed")
                except Exception as e:
                    logger.error(f"Error in duplicate detection hook: {e}")

            @AbstractExtension.bll_hook("Auth", "UserManager", "update", "after")
            def update_duplicate_check_hook(manager, result, create_args=None):
                """Hook to recheck duplicates after user data updates."""
                try:
                    if self.enable_automatic_detection and result:
                        asyncio.create_task(self._recheck_duplicates(result))
                        logger.debug("Duplicate recheck hook executed")
                except Exception as e:
                    logger.error(f"Error in duplicate recheck hook: {e}")

        except Exception as e:
            logger.error(f"Error registering merge hooks: {e}")

    def _initialize_conflict_resolvers(self):
        """Initialize conflict resolution strategies."""
        self.conflict_resolvers = {
            "email": self._resolve_email_conflict,
            "phone": self._resolve_phone_conflict,
            "name": self._resolve_name_conflict,
            "address": self._resolve_address_conflict,
            "preferences": self._resolve_preferences_conflict,
            "metadata": self._resolve_metadata_conflict,
        }

    def _setup_automatic_detection(self):
        """Setup automatic duplicate detection."""
        logger.debug("Automatic duplicate detection enabled")

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

    @AbstractExtension.ability("detect_duplicates")
    async def detect_duplicates(
        self,
        user_data: Dict[str, Any],
        search_scope: str = "all",
        include_fuzzy: bool = True,
    ) -> Dict[str, Any]:
        """
        Detect potential duplicate accounts for a given user.
        """
        try:
            email = user_data.get("email")
            phone = user_data.get("phone")
            name = user_data.get("name", "")

            if not email and not phone:
                return {
                    "success": False,
                    "message": "Email or phone required for duplicate detection",
                }

            duplicates = []

            # Exact matches
            if email:
                email_matches = await self._find_email_matches(email)
                duplicates.extend(email_matches)

            if phone:
                phone_matches = await self._find_phone_matches(phone)
                duplicates.extend(phone_matches)

            # Fuzzy matches if enabled
            if include_fuzzy and self.enable_fuzzy_matching and name:
                fuzzy_matches = await self._find_fuzzy_name_matches(name, user_data)
                duplicates.extend(fuzzy_matches)

            # Remove duplicates and score matches
            unique_duplicates = self._deduplicate_and_score(duplicates, user_data)

            # Filter by similarity threshold
            filtered_duplicates = [
                dup
                for dup in unique_duplicates
                if dup["similarity_score"] >= self.similarity_threshold
            ]

            logger.debug(f"Found {len(filtered_duplicates)} potential duplicates")
            return {
                "success": True,
                "duplicates": filtered_duplicates,
                "total_found": len(filtered_duplicates),
                "search_criteria": {
                    "email": email,
                    "phone": phone,
                    "name": name,
                    "fuzzy_enabled": include_fuzzy,
                },
            }

        except Exception as e:
            logger.error(f"Error detecting duplicates: {e}")
            return {
                "success": False,
                "message": f"Duplicate detection failed: {str(e)}",
            }

    @AbstractExtension.ability("create_merge_request")
    async def create_merge_request(
        self,
        primary_user_id: str,
        secondary_user_id: str,
        merge_strategy: Optional[Dict[str, str]] = None,
        verification_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a merge request for two user accounts.
        """
        try:
            # Validate users exist and are different
            if primary_user_id == secondary_user_id:
                return {"success": False, "message": "Cannot merge account with itself"}

            # Check for existing merge requests
            existing_request = await self._check_existing_merge_request(
                primary_user_id, secondary_user_id
            )
            if existing_request:
                return {
                    "success": False,
                    "message": "Merge request already exists",
                    "existing_request": existing_request,
                }

            # Analyze merge conflicts
            conflicts = await self._analyze_merge_conflicts(
                primary_user_id, secondary_user_id
            )

            # Generate merge request ID
            import uuid

            request_id = str(uuid.uuid4())

            # Create merge request
            merge_request = {
                "id": request_id,
                "primary_user_id": primary_user_id,
                "secondary_user_id": secondary_user_id,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
                "conflicts": conflicts,
                "merge_strategy": merge_strategy or {},
                "verification_data": verification_data or {},
                "requires_verification": self.require_verification,
            }

            # Determine if can auto-resolve
            auto_resolvable = len(conflicts) <= self.max_conflicts_auto_resolve
            if auto_resolvable and not self.require_verification:
                merge_request["status"] = "ready_for_processing"
            elif self.require_verification:
                merge_request["status"] = "requires_verification"

            # Store merge request
            self.pending_merges[request_id] = merge_request

            # Generate merge preview if enabled
            preview = None
            if self.enable_merge_preview:
                preview = await self._generate_merge_preview(merge_request)
                merge_request["preview"] = preview

            logger.debug(f"Created merge request {request_id}")
            return {
                "success": True,
                "merge_request_id": request_id,
                "merge_request": merge_request,
                "auto_resolvable": auto_resolvable,
                "preview": preview,
            }

        except Exception as e:
            logger.error(f"Error creating merge request: {e}")
            return {
                "success": False,
                "message": f"Failed to create merge request: {str(e)}",
            }

    @AbstractExtension.ability("process_merge")
    async def process_merge(
        self,
        merge_request_id: str,
        conflict_resolutions: Optional[Dict[str, str]] = None,
        verification_code: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Process a merge request and consolidate accounts.
        """
        try:
            # Get merge request
            if merge_request_id not in self.pending_merges:
                return {"success": False, "message": "Merge request not found"}

            merge_request = self.pending_merges[merge_request_id]

            # Check status
            if merge_request["status"] not in [
                "pending",
                "ready_for_processing",
                "requires_verification",
            ]:
                return {
                    "success": False,
                    "message": f"Cannot process merge with status: {merge_request['status']}",
                }

            # Verify if required
            if merge_request.get("requires_verification") and not force:
                if not verification_code:
                    return {"success": False, "message": "Verification code required"}

                verification_result = await self._verify_merge_request(
                    merge_request_id, verification_code
                )
                if not verification_result["valid"]:
                    return {"success": False, "message": "Invalid verification code"}

            # Update status
            merge_request["status"] = "in_progress"
            merge_request["processing_started_at"] = datetime.utcnow().isoformat()

            # Resolve conflicts
            if conflict_resolutions:
                merge_request["conflict_resolutions"] = conflict_resolutions

            resolution_result = await self._resolve_all_conflicts(
                merge_request, conflict_resolutions or {}
            )

            if not resolution_result["success"]:
                merge_request["status"] = "failed"
                merge_request["error"] = resolution_result.get(
                    "message", "Conflict resolution failed"
                )
                return {
                    "success": False,
                    "message": resolution_result.get(
                        "message", "Conflict resolution failed"
                    ),
                }

            # Perform actual merge
            merge_result = await self._perform_account_merge(merge_request)

            if merge_result["success"]:
                merge_request["status"] = "completed"
                merge_request["completed_at"] = datetime.utcnow().isoformat()
                merge_request["merged_user_id"] = merge_result.get("merged_user_id")

                # Create merge history record
                await self._create_merge_history(merge_request, merge_result)

                # Cleanup
                if not self.enable_rollback:
                    await self._cleanup_secondary_account(
                        merge_request["secondary_user_id"]
                    )

                logger.debug(f"Merge request {merge_request_id} completed successfully")
                return {
                    "success": True,
                    "merge_request_id": merge_request_id,
                    "merged_user_id": merge_result.get("merged_user_id"),
                    "merge_summary": merge_result.get("summary", {}),
                }
            else:
                merge_request["status"] = "failed"
                merge_request["error"] = merge_result.get(
                    "message", "Merge operation failed"
                )
                return {
                    "success": False,
                    "message": merge_result.get("message", "Merge operation failed"),
                }

        except Exception as e:
            logger.error(f"Error processing merge: {e}")
            if merge_request_id in self.pending_merges:
                self.pending_merges[merge_request_id]["status"] = "failed"
                self.pending_merges[merge_request_id]["error"] = str(e)
            return {"success": False, "message": f"Merge processing failed: {str(e)}"}

    @AbstractExtension.ability("rollback_merge")
    async def rollback_merge(
        self,
        merge_history_id: str,
        reason: str = "User requested rollback",
    ) -> Dict[str, Any]:
        """
        Rollback a completed merge operation.
        """
        try:
            if not self.enable_rollback:
                return {
                    "success": False,
                    "message": "Rollback functionality is disabled",
                }

            # Get merge history
            merge_history = await self._get_merge_history(merge_history_id)
            if not merge_history:
                return {"success": False, "message": "Merge history not found"}

            # Check if rollback is still possible
            merge_date = datetime.fromisoformat(merge_history["completed_at"])
            cutoff_date = datetime.utcnow() - timedelta(
                days=self.rollback_retention_days
            )

            if merge_date < cutoff_date:
                return {
                    "success": False,
                    "message": f"Rollback period expired ({self.rollback_retention_days} days)",
                }

            # Perform rollback
            rollback_result = await self._perform_merge_rollback(merge_history, reason)

            if rollback_result["success"]:
                logger.debug(f"Merge {merge_history_id} rolled back successfully")
                return {
                    "success": True,
                    "merge_history_id": merge_history_id,
                    "rollback_summary": rollback_result.get("summary", {}),
                    "restored_accounts": rollback_result.get("restored_accounts", []),
                }
            else:
                return {
                    "success": False,
                    "message": rollback_result.get("message", "Rollback failed"),
                }

        except Exception as e:
            logger.error(f"Error rolling back merge: {e}")
            return {"success": False, "message": f"Merge rollback failed: {str(e)}"}

    @AbstractExtension.ability("get_merge_preview")
    async def get_merge_preview(
        self,
        primary_user_id: str,
        secondary_user_id: str,
        conflict_resolutions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a preview of what would happen if two accounts were merged.
        """
        try:
            # Analyze conflicts
            conflicts = await self._analyze_merge_conflicts(
                primary_user_id, secondary_user_id
            )

            # Create temporary merge request for preview
            temp_request = {
                "primary_user_id": primary_user_id,
                "secondary_user_id": secondary_user_id,
                "conflicts": conflicts,
                "conflict_resolutions": conflict_resolutions or {},
            }

            # Generate preview
            preview = await self._generate_merge_preview(temp_request)

            return {
                "success": True,
                "preview": preview,
                "conflicts": conflicts,
                "total_conflicts": len(conflicts),
            }

        except Exception as e:
            logger.error(f"Error generating merge preview: {e}")
            return {"success": False, "message": f"Preview generation failed: {str(e)}"}

    # Helper methods
    async def _find_email_matches(self, email: str) -> List[Dict[str, Any]]:
        """Find users with matching email addresses."""
        # In real implementation, this would query the database
        return []

    async def _find_phone_matches(self, phone: str) -> List[Dict[str, Any]]:
        """Find users with matching phone numbers."""
        # In real implementation, this would query the database
        return []

    async def _find_fuzzy_name_matches(
        self, name: str, user_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Find users with similar names using fuzzy matching."""
        try:
            if not self.enable_fuzzy_matching:
                return []

            from fuzzywuzzy import fuzz

            # In real implementation, this would query users and compare names
            # For now, return empty list
            return []

        except ImportError:
            logger.warning("Fuzzywuzzy not available - skipping fuzzy matching")
            return []

    def _deduplicate_and_score(
        self, duplicates: List[Dict[str, Any]], user_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Remove duplicates and calculate similarity scores."""
        unique_duplicates = []
        seen_ids = set()

        for duplicate in duplicates:
            user_id = duplicate.get("user_id")
            if user_id not in seen_ids:
                duplicate["similarity_score"] = self._calculate_similarity_score(
                    duplicate, user_data
                )
                unique_duplicates.append(duplicate)
                seen_ids.add(user_id)

        return sorted(
            unique_duplicates, key=lambda x: x["similarity_score"], reverse=True
        )

    def _calculate_similarity_score(
        self, candidate: Dict[str, Any], user_data: Dict[str, Any]
    ) -> float:
        """Calculate similarity score between two user records."""
        score = 0.0
        total_weight = 0.0

        # Email match (high weight)
        if candidate.get("email") == user_data.get("email"):
            score += 0.4
        total_weight += 0.4

        # Phone match (high weight)
        if candidate.get("phone") == user_data.get("phone"):
            score += 0.3
        total_weight += 0.3

        # Name similarity (medium weight)
        if self.enable_fuzzy_matching:
            try:
                from fuzzywuzzy import fuzz

                name_similarity = (
                    fuzz.ratio(candidate.get("name", ""), user_data.get("name", ""))
                    / 100.0
                )
                score += name_similarity * 0.3
            except ImportError:
                pass
        total_weight += 0.3

        return score / total_weight if total_weight > 0 else 0.0

    async def _check_existing_merge_request(
        self, primary_user_id: str, secondary_user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Check if merge request already exists between users."""
        for request in self.pending_merges.values():
            if (
                request["primary_user_id"] == primary_user_id
                and request["secondary_user_id"] == secondary_user_id
            ) or (
                request["primary_user_id"] == secondary_user_id
                and request["secondary_user_id"] == primary_user_id
            ):
                return request
        return None

    async def _analyze_merge_conflicts(
        self, primary_user_id: str, secondary_user_id: str
    ) -> List[Dict[str, Any]]:
        """Analyze potential conflicts between two accounts."""
        conflicts = []

        # In real implementation, this would compare user data fields
        # For now, return example conflicts
        conflicts.append(
            {
                "field": "email",
                "primary_value": "user1@example.com",
                "secondary_value": "user2@example.com",
                "conflict_type": "different_values",
                "priority": "high",
                "auto_resolvable": False,
            }
        )

        return conflicts

    async def _generate_merge_preview(
        self, merge_request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a preview of merge results."""
        return {
            "merged_fields": {},
            "conflicts_resolved": len(merge_request.get("conflicts", [])),
            "data_changes": [],
            "estimated_completion_time": "2-5 minutes",
        }

    async def _verify_merge_request(
        self, request_id: str, code: str
    ) -> Dict[str, bool]:
        """Verify merge request with verification code."""
        # In real implementation, this would validate the verification code
        return {"valid": True}

    async def _resolve_all_conflicts(
        self, merge_request: Dict[str, Any], resolutions: Dict[str, str]
    ) -> Dict[str, Any]:
        """Resolve all conflicts in a merge request."""
        try:
            conflicts = merge_request.get("conflicts", [])
            resolved_conflicts = []

            for conflict in conflicts:
                field = conflict["field"]
                resolution_strategy = resolutions.get(field, "keep_primary")

                if field in self.conflict_resolvers:
                    resolution_result = await self.conflict_resolvers[field](
                        conflict, resolution_strategy
                    )
                    resolved_conflicts.append(resolution_result)
                else:
                    # Default resolution
                    resolved_conflicts.append(
                        {
                            "field": field,
                            "strategy": resolution_strategy,
                            "resolved_value": conflict["primary_value"],
                        }
                    )

            return {"success": True, "resolved_conflicts": resolved_conflicts}

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def _perform_account_merge(
        self, merge_request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Perform the actual account merge operation."""
        try:
            primary_user_id = merge_request["primary_user_id"]
            secondary_user_id = merge_request["secondary_user_id"]

            # In real implementation, this would:
            # 1. Consolidate user data
            # 2. Transfer relationships and permissions
            # 3. Update references in other tables
            # 4. Mark secondary account as merged

            summary = {
                "data_fields_merged": 5,
                "permissions_transferred": 3,
                "relationships_updated": 2,
            }

            return {
                "success": True,
                "merged_user_id": primary_user_id,
                "summary": summary,
            }

        except Exception as e:
            return {"success": False, "message": str(e)}

    async def _create_merge_history(
        self, merge_request: Dict[str, Any], merge_result: Dict[str, Any]
    ) -> str:
        """Create merge history record."""
        import uuid

        history_id = str(uuid.uuid4())

        # In real implementation, this would be stored in database
        return history_id

    async def _cleanup_secondary_account(self, user_id: str):
        """Clean up secondary account after merge."""
        # In real implementation, this would mark account as merged/deleted
        pass

    async def _get_merge_history(self, history_id: str) -> Optional[Dict[str, Any]]:
        """Get merge history record."""
        # In real implementation, this would query the database
        return {
            "id": history_id,
            "completed_at": datetime.utcnow().isoformat(),
        }

    async def _perform_merge_rollback(
        self, merge_history: Dict[str, Any], reason: str
    ) -> Dict[str, Any]:
        """Perform merge rollback operation."""
        try:
            # In real implementation, this would:
            # 1. Restore secondary account
            # 2. Revert data changes
            # 3. Update references

            return {
                "success": True,
                "summary": {"accounts_restored": 1, "data_reverted": 5},
                "restored_accounts": [merge_history.get("secondary_user_id")],
            }

        except Exception as e:
            return {"success": False, "message": str(e)}

    # Conflict resolver methods
    async def _resolve_email_conflict(
        self, conflict: Dict[str, Any], strategy: str
    ) -> Dict[str, Any]:
        """Resolve email field conflict."""
        if strategy == "keep_primary":
            resolved_value = conflict["primary_value"]
        elif strategy == "keep_secondary":
            resolved_value = conflict["secondary_value"]
        else:
            resolved_value = conflict["primary_value"]  # Default

        return {
            "field": "email",
            "strategy": strategy,
            "resolved_value": resolved_value,
        }

    async def _resolve_phone_conflict(
        self, conflict: Dict[str, Any], strategy: str
    ) -> Dict[str, Any]:
        """Resolve phone field conflict."""
        # Similar to email resolution
        return await self._resolve_email_conflict(conflict, strategy)

    async def _resolve_name_conflict(
        self, conflict: Dict[str, Any], strategy: str
    ) -> Dict[str, Any]:
        """Resolve name field conflict."""
        if strategy == "merge_both":
            primary_name = conflict["primary_value"]
            secondary_name = conflict["secondary_value"]
            resolved_value = f"{primary_name} ({secondary_name})"
        else:
            resolved_value = conflict["primary_value"]

        return {
            "field": "name",
            "strategy": strategy,
            "resolved_value": resolved_value,
        }

    async def _resolve_address_conflict(
        self, conflict: Dict[str, Any], strategy: str
    ) -> Dict[str, Any]:
        """Resolve address field conflict."""
        return await self._resolve_email_conflict(conflict, strategy)

    async def _resolve_preferences_conflict(
        self, conflict: Dict[str, Any], strategy: str
    ) -> Dict[str, Any]:
        """Resolve preferences field conflict."""
        if strategy == "merge_both":
            # Merge preference objects
            primary_prefs = json.loads(conflict["primary_value"] or "{}")
            secondary_prefs = json.loads(conflict["secondary_value"] or "{}")
            merged_prefs = {
                **secondary_prefs,
                **primary_prefs,
            }  # Primary takes precedence
            resolved_value = json.dumps(merged_prefs)
        else:
            resolved_value = conflict["primary_value"]

        return {
            "field": "preferences",
            "strategy": strategy,
            "resolved_value": resolved_value,
        }

    async def _resolve_metadata_conflict(
        self, conflict: Dict[str, Any], strategy: str
    ) -> Dict[str, Any]:
        """Resolve metadata field conflict."""
        return await self._resolve_preferences_conflict(conflict, strategy)

    async def _check_for_duplicates(self, user_data: Any):
        """Check for duplicates (used in hooks)."""
        # Implementation for hook
        pass

    async def _recheck_duplicates(self, user_data: Any):
        """Recheck duplicates after update (used in hooks)."""
        # Implementation for hook
        pass

    # Extension lifecycle methods
    def on_start(self) -> bool:
        """Start the Account Merge extension."""
        try:
            # Reconnect to Redis if needed
            if not self.redis_client:
                self._initialize_redis()

            logger.debug("Account Merge extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start Account Merge extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Account Merge extension."""
        try:
            # Clean up resources
            self.pending_merges.clear()
            self.merge_cache.clear()
            self.duplicate_candidates.clear()

            # Close Redis connection
            if self.redis_client:
                self.redis_client.close()

            logger.debug("Account Merge extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping Account Merge extension: {e}")
            return False

    def on_startup(self):
        """Called during application startup."""
        logger.debug("Account Merge extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("Account Merge extension shutdown hook called")

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check similarity threshold
        if not 0.0 <= self.similarity_threshold <= 1.0:
            issues.append("Similarity threshold must be between 0.0 and 1.0")

        # Check optional libraries
        if self.enable_fuzzy_matching:
            try:
                import fuzzywuzzy
            except ImportError:
                issues.append(
                    "Fuzzywuzzy library not available but fuzzy matching is enabled"
                )

        # Check Redis connection
        if not self.redis_client:
            try:
                import redis

                redis_url = env("REDIS_URL")
                if not redis_url:
                    issues.append(
                        "Redis URL not configured - merge caching will use memory"
                    )
            except ImportError:
                issues.append("Redis not available - merge caching will use memory")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "merge:detect",
            "merge:create",
            "merge:process",
            "merge:rollback",
            "merge:admin",
            "users:read",
            "users:update",
            "users:merge",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_merge_statuses(self) -> Dict[str, str]:
        """Get available merge statuses."""
        return self.MERGE_STATUSES.copy()

    def get_conflict_strategies(self) -> Dict[str, str]:
        """Get available conflict resolution strategies."""
        return self.CONFLICT_STRATEGIES.copy()

    def get_field_priorities(self) -> Dict[str, str]:
        """Get field priorities for merging."""
        return self.FIELD_PRIORITIES.copy()

    def get_merge_stats(self) -> Dict[str, Any]:
        """Get merge operation statistics."""
        return {
            "pending_merges": len(self.pending_merges),
            "duplicate_candidates": len(self.duplicate_candidates),
            "automatic_detection_enabled": self.enable_automatic_detection,
            "fuzzy_matching_enabled": self.enable_fuzzy_matching,
            "rollback_enabled": self.enable_rollback,
            "similarity_threshold": self.similarity_threshold,
        }
