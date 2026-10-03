import os
import uuid
from typing import Any, Dict, List, Optional

import pytest
from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity, SkipThisTest
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.endpoints.EP_Auth_test import (
    TestUserAndSessionEndpoints as CoreUserAndSessionEndpointTests,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.conversations.BLL_Conversations import (
    ArtifactModel,
    ConversationModel,
    FeedbackModel,
    MessageModel,
)
from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations
from zephyrex.lib.Environment import refresh_settings

# This module's `server`/`admin_a` fixtures (ExtensionServerMixin) are
# module-scoped: a single JWT is minted once and reused by every test
# method below. The shared autouse fixture
# `zephyrex.testing.fixtures._restore_settings_after_test` force-sets
# JWT_AUDIENCE/JWT_ISSUER to "test-aud"/"test-iss" after EVERY test in the
# session, but those env vars default to AppSettings' "zephyrex" until the
# *first* test's teardown ever runs. When this module happens to be the
# first one executed in a worker, `admin_a`'s token gets minted with
# iss/aud="zephyrex" during test #1 (still unpoisoned), and every later
# test in this module then verifies that same token against the
# now-poisoned "test-iss"/"test-aud" values, raising `InvalidIssuerError`
# surfaced as a bogus 401 "Invalid token". Pin the same stable values up
# front (idempotent with what the framework fixture converges to anyway)
# so minting and verification always agree regardless of run order.
os.environ["JWT_AUDIENCE"] = "test-aud"
os.environ["JWT_ISSUER"] = "test-iss"
refresh_settings()

# Initialize faker
faker = Faker()


@pytest.mark.ep
@pytest.mark.conversations
class TestConversationEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Conversation Management endpoints."""

    # Extension configuration
    extension_class = EXT_Conversations
    class_under_test = ConversationModel

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # Base endpoint configuration
    base_endpoint = "conversation"
    entity_name = "conversation"
    string_field_to_update = "name"
    required_fields = ["id", "name", "created_at"]
    system_entity = False

    # Search configuration
    supports_search = True
    searchable_fields = ["name", "description", "team_id"]
    search_example_value = "Test Conversation"

    # Test data generation
    create_fields = {
        "name": lambda: f"test_conversation_{faker.uuid4()}",
        "description": lambda: faker.sentence(),
        "user_id": None,  # Will be populated from parent entities
    }
    update_fields = {
        "name": lambda: f"updated_conversation_{faker.uuid4()}",
        "description": "Updated conversation description",
    }
    unique_fields = ["name"]

    # Parent entities for conversations. `user_id` is genuinely optional on
    # ConversationModel.Create (UserModel.Reference.ID.Optional) -- a
    # conversation is owned by either a user (direct/1:1 chats, see
    # ConversationManager.create_direct_message) or a team (see
    # ai_agents.SVC_AI_Agents._create_team_conversation, which creates
    # conversations with team_id but no user_id at all). Declaring it
    # non-nullable here would contradict that contract and, if the BLL were
    # tightened to match, would break the team-conversation creation path in
    # the ai_agents extension.
    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            nullable=True,
            system=False,
            path_level=None,  # Not in path
            test_class=CoreUserAndSessionEndpointTests,
        ),
    ]

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GQL_mutation_delete",
            details="GraphQL delete not implemented yet",
        ),
        SkipThisTest(
            name="test_PUT_200",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PATCH_200_rename",
            details="Not Implemented: Rename conversation using AI",
        ),
        SkipThisTest(
            name="test_PUT_404_nonexistent",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_400",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_422",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_GQL_mutation_create",
            details="GraphQL mutation needs all required fields in test",
        ),
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="GraphQL mutation needs all required fields in test",
        ),
    ]

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for conversation creation.

        Args:
            name: Optional name for the conversation
            parent_ids: Optional parent IDs (not used for conversations)
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing conversation creation payload
        """
        name = name or f"Test Conversation {faker.catch_phrase()}"

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "name": 12345,  # Invalid: number instead of string
                "description": None,  # Invalid: null for optional field
                "is_group_chat": "yes",  # Invalid: string instead of boolean
            }
        elif minimal:
            # Only include required fields
            payload = {
                "name": name,
                "description": "Minimal test conversation",
            }
        else:
            # Include all fields
            payload = {
                "name": name,
                "description": f"Test conversation description for {name}",
                "is_group_chat": False,
            }

        return payload

    def test_PATCH_200_rename(
        self, server: Any, admin_a: Any, team_a: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Test renaming a conversation using AI.

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            Dict containing renamed conversation
        """
        if self.reason_to_skip("test_PATCH_200_rename"):
            pytest.skip("Skipping test_PATCH_200_rename")

        # First create a conversation with some messages
        conversation = self.test_POST_201(server, admin_a, team_a)
        print(f"Created conversation: {conversation}")

        # Test message endpoints creates messages in this conversation
        message_tests = TestMessageEndpoints()
        message = message_tests.test_POST_201(
            server,
            admin_a,
            team_a,
            parent_ids={"conversation_id": conversation["id"]},
        )

        # Call the rename endpoint
        endpoint = f"/v1/{self.base_endpoint}?id={conversation['id']}"
        response = server.patch(
            endpoint,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            response,
            200,
            "PATCH rename",
            endpoint,
        )

        renamed_conversation = self._assert_entity_in_response(response)

        if renamed_conversation["id"] != conversation["id"]:
            raise AssertionError(
                f"[{self.entity_name}] Conversation ID changed\n"
                f"Expected: {conversation['id']}\n"
                f"Actual: {renamed_conversation['id']}"
            )

        if renamed_conversation["name"] == conversation["name"]:
            raise AssertionError(
                f"[{self.entity_name}] Conversation name not changed\n"
                f"Name still set to: {renamed_conversation['name']}"
            )

        return renamed_conversation

    def test_POST_201_batch(
        self, server: Any, admin_a: Any, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test batch creation of conversations.

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            List of created conversations
        """
        if self.reason_to_skip("test_POST_201_batch"):
            pytest.skip("Skipping test_POST_201_batch")

        # Create multiple conversations with batch API
        batch_size = 3
        batch = []

        for i in range(batch_size):
            name = f"Batch Conversation {i+1} {uuid.uuid4()}"
            batch.append(
                {
                    "name": name,
                    "description": f"Description for batch conversation {i+1}",
                    "is_group_chat": i % 2
                    == 0,  # Alternate between group and individual
                }
            )

        payload = {"conversations": batch}

        response = server.post(
            f"/v1/{self.base_endpoint}",
            json=payload,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            response, 201, "POST batch create", f"/v1/{self.base_endpoint}", payload
        )
        json_response = response.json()

        if "conversations" not in json_response:
            raise AssertionError(
                f"[{self.entity_name}] Conversations not found in batch creation response\n"
                f"Response: {json_response}"
            )

        conversations = json_response["conversations"]
        if not isinstance(conversations, list):
            raise AssertionError(
                f"[{self.entity_name}] Batch response should be a list\n"
                f"Response: {conversations}"
            )

        if len(conversations) != batch_size:
            raise AssertionError(
                f"[{self.entity_name}] Expected {batch_size} conversations, got {len(conversations)}\n"
                f"Conversations: {conversations}"
            )

        # Verify each batch item was created properly
        for i, conversation in enumerate(conversations):
            if conversation["name"] != batch[i]["name"]:
                raise AssertionError(
                    f"[{self.entity_name}] Batch item name mismatch\n"
                    f"Expected: {batch[i]['name']}\n"
                    f"Got: {conversation['name']}"
                )
            if conversation["is_group_chat"] != batch[i]["is_group_chat"]:
                raise AssertionError(
                    f"[{self.entity_name}] Batch item is_group_chat mismatch\n"
                    f"Expected: {batch[i]['is_group_chat']}\n"
                    f"Got: {conversation['is_group_chat']}"
                )

        return conversations


@pytest.mark.ep
@pytest.mark.messages
class TestMessageEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Message Management endpoints."""

    # Extension configuration
    extension_class = EXT_Conversations
    class_under_test = MessageModel

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # Base endpoint configuration
    base_endpoint = "message"
    entity_name = "message"
    string_field_to_update = "content"
    required_fields = ["id", "content", "conversation_id", "created_at"]
    system_entity = False

    # Search configuration
    supports_search = True
    searchable_fields = ["content", "conversation_id"]
    search_example_value = "Test Message"

    # Test data generation
    create_fields = {
        "content": lambda: f"test_message_{faker.text()}",
        "conversation_id": None,  # Will be populated from parent entities
    }
    update_fields = {
        "content": lambda: f"Updated message content {faker.text()}",
    }
    unique_fields = []

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GQL_mutation_delete",
            details="GraphQL delete not implemented yet",
        ),
        SkipThisTest(
            name="test_PUT_200",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PATCH_201_fork",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_404_nonexistent",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_400",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_422",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_GQL_mutation_create",
            details="GraphQL mutation needs all required fields in test",
        ),
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="GraphQL mutation needs all required fields in test",
        ),
    ]

    # Define dependency on conversations
    parent_entities = [
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            nullable=False,
            system=False,
            path_level=1,
            test_class=TestConversationEndpoints,
        ),
    ]

    NESTING_CONFIG_OVERRIDES = {
        "LIST": 0,
        "CREATE": 0,
        "DETAIL": 0,
        "SEARCH": 0,
    }

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for message creation.

        Args:
            name: Optional content for the message
            parent_ids: Optional parent IDs (conversation_id required)
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing message creation payload
        """
        content = name or f"Test message: {faker.text()}"

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "content": 12345,  # Invalid: number instead of string
            }
        elif minimal:
            # Only include required fields
            payload = {
                "content": content,
            }
        else:
            # Include all fields
            payload = {
                "content": content,
            }

        # Add conversation_id if provided
        if parent_ids and "conversation_id" in parent_ids:
            # Handle both object and dictionary access patterns
            conv_id = (
                parent_ids["conversation_id"].id
                if hasattr(parent_ids["conversation_id"], "id")
                else parent_ids["conversation_id"]
            )
            payload["conversation_id"] = conv_id

        return payload

    def test_PATCH_201_fork(
        self, server: Any, admin_a: Any, team_a: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Test editing a message by creating a new message with the same parent (fork).

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            Dict containing forked message
        """
        if self.reason_to_skip("test_PATCH_201_fork"):
            pytest.skip("Skipping test_PATCH_201_fork")

        message = self.test_POST_201(server, admin_a, team_a)
        parent_path = (
            f"/v1/conversation/{message['conversation_id']}/message/{message['id']}"
        )
        new_content = f"Edited message content {uuid.uuid4()}"

        response = server.patch(
            parent_path,
            json={"content": new_content},  # Send content embedded in JSON
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(
            response,
            201,
            "PATCH fork",
            parent_path,
        )

        edited_message = self._assert_entity_in_response(response)

        if edited_message["content"] != new_content:
            raise AssertionError(
                f"[{self.entity_name}] Message content not updated\n"
                f"Expected: {new_content}\n"
                f"Actual: {edited_message['content']}"
            )

        if edited_message["id"] == message["id"]:
            raise AssertionError(
                f"[{self.entity_name}] Message ID not changed (fork should create new message)\n"
                f"ID still: {edited_message['id']}"
            )

        if edited_message["conversation_id"] != message["conversation_id"]:
            raise AssertionError(
                f"[{self.entity_name}] Conversation ID changed\n"
                f"Expected: {message['conversation_id']}\n"
                f"Actual: {edited_message['conversation_id']}"
            )

        return edited_message


@pytest.mark.ep
@pytest.mark.feedback
class TestFeedbackEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Feedback Management endpoints."""

    # Extension configuration
    extension_class = EXT_Conversations
    class_under_test = FeedbackModel

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # Base endpoint configuration
    base_endpoint = "feedback"
    entity_name = "feedback"
    string_field_to_update = "content"
    required_fields = ["id", "content", "positive", "message_id", "created_at"]
    system_entity = False

    # Search configuration
    supports_search = True
    searchable_fields = ["content", "positive", "message_id"]
    search_example_value = "Test Feedback"

    # Test data generation
    create_fields = {
        "content": lambda: f"test_feedback_{faker.text()}",
        "positive": True,
        "message_id": None,  # Will be populated from parent entities
    }
    update_fields = {
        "content": lambda: f"Updated feedback: {faker.text()}",
        "positive": False,
    }
    unique_fields = []

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GQL_mutation_delete",
            details="GraphQL delete not implemented yet",
        ),
        SkipThisTest(
            name="test_PUT_200",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_404_nonexistent",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_GET_200_positive_feedback",
            details="Filtering has not been implemented yet",
        ),
        SkipThisTest(
            name="test_PUT_400",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_422",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_GQL_mutation_create",
            details="GraphQL mutation needs all required fields in test",
        ),
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="GraphQL mutation needs all required fields in test",
        ),
    ]

    # Feedback's only genuine foreign key is message_id -- FeedbackModel has
    # no conversation_id column (conversation is reachable only
    # transitively, via message.conversation_id). A "conversation"
    # ParentEntity used to be declared here too (for the nested
    # /v1/conversation/{id}/message/{id}/feedback URL used by the skipped
    # test_GET_200_positive_feedback), but AbstractEPTest's
    # _get_navigation_properties() unconditionally treats every declared
    # parent's name as a candidate `include=` value -- so it made the
    # generic includes tests request `include=conversation`, which the
    # endpoint correctly rejects (Feedback has no direct conversation
    # relationship to include). Creating a "message" parent already creates
    # its own "conversation" parent recursively (see
    # TestMessageEndpoints.parent_entities), so the full
    # conversation -> message -> feedback chain still gets built without
    # declaring conversation a second time here.
    parent_entities = [
        ParentEntity(
            name="message",
            foreign_key="message_id",
            nullable=False,
            path_level=1,
            test_class=TestMessageEndpoints,
        ),
    ]

    # NESTING_CONFIG_OVERRIDES = {
    #     "LIST": 2,
    #     "CREATE": 2,
    #     "DETAIL": 0,
    #     "SEARCH": 0,
    # }

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for feedback creation.

        Args:
            name: Optional content for the feedback
            parent_ids: Optional parent IDs (message_id required)
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing feedback creation payload
        """
        content = name or f"Test feedback: {faker.text()}"

        print(f"DEBUG: create_payload called with parent_ids: {parent_ids}")

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "content": 12345,  # Invalid: number instead of string
                "positive": "yes",  # Invalid: string instead of boolean
            }
        elif minimal:
            # Only include required fields
            payload = {
                "content": content,
                "positive": True,
            }
        else:
            # Include all fields
            payload = {
                "content": content,
                "positive": True,
            }

        # Add message_id if provided
        if parent_ids and "message_id" in parent_ids:
            payload["message_id"] = parent_ids["message_id"]
            print(f"DEBUG: Added message_id to payload: {payload['message_id']}")

        print(f"DEBUG: Final payload: {payload}")
        return payload

    def test_GET_200_positive_feedback(
        self, server: Any, admin_a: Any, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test retrieving positive feedback.

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            List of positive feedback items
        """
        if self.reason_to_skip("test_GET_200_positive_feedback"):
            pytest.skip("Skipping test_GET_200_positive_feedback")

        # First create some feedback
        feedback1 = self.test_POST_201(server, admin_a, team_a)

        # Create negative feedback
        negative_payload = self.create_payload()
        negative_payload["positive"] = False
        negative_payload["message_id"] = feedback1["message_id"]

        response = server.post(
            f"/v1/conversation/{feedback1['conversation_id']}/message/{feedback1['message_id']}/feedback",
            json=negative_payload,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        self._assert_response_status(response, 201, "POST negative feedback", "")

        # Get only positive feedback
        endpoint = f"/v1/feedback?positive=true"
        response = server.get(
            endpoint,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(response, 200, "GET positive feedback", endpoint)
        json_response = response.json()

        if "feedbacks" not in json_response and "feedback" not in json_response:
            raise AssertionError(
                f"[{self.entity_name}] Feedback list not found in response\n"
                f"Response: {json_response}"
            )

        feedbacks = json_response.get("feedbacks", json_response.get("feedback", []))

        # Verify all returned feedback is positive
        for feedback in feedbacks:
            if not feedback.get("positive", False):
                raise AssertionError(
                    f"[{self.entity_name}] Non-positive feedback returned\n"
                    f"Feedback: {feedback}"
                )

        return feedbacks


@pytest.mark.ep
@pytest.mark.artifacts
class TestArtifactEndpoints(AbstractEPTest, ExtensionServerMixin):
    """Tests for the Artifact Management endpoints (within conversation context)."""

    # Extension configuration
    extension_class = EXT_Conversations
    class_under_test = ArtifactModel

    # Test configuration
    test_config = ClassOfTestsConfig(
        categories=[
            CategoryOfTest.ENDPOINT,
            CategoryOfTest.REST,
            CategoryOfTest.EXTENSION,
        ],
        timeout=60,
        cleanup=True,
    )

    # Base endpoint configuration
    base_endpoint = "artifact"
    entity_name = "artifact"
    string_field_to_update = "name"
    required_fields = [
        "id",
        "name",
        "relative_path",
        "hosted_path",
        "created_at",
        "updated_at",
        "conversation_id",  # Now required in base definition
    ]
    system_entity = False

    # Search configuration
    supports_search = True
    searchable_fields = [
        "name",
        "relative_path",
        "conversation_id",
        "message_id",
    ]
    search_example_value = "Test Artifact"

    # Test data generation
    create_fields = {
        "name": lambda: f"test_artifact_{faker.uuid4()}",
        "relative_path": lambda: f"test_artifacts/test_{faker.uuid4()}.txt",
        "hosted_path": lambda: f"/static/test_artifacts/test_{faker.uuid4()}.txt",
        "content": lambda: faker.text(),
        "encrypted": False,
        "message_id": None,  # Will be populated from parent entities
    }
    update_fields = {
        "name": lambda: f"updated_artifact_{faker.uuid4()}",
        "content": lambda: f"Updated content: {faker.text()}",
    }
    unique_fields = []

    # Tests to skip (if any)
    _skip_tests = [
        SkipThisTest(
            name="test_GQL_mutation_delete",
            details="GraphQL delete not implemented yet",
        ),
        SkipThisTest(
            name="test_PUT_200",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_GET_200_encrypted_artifacts",
            details="Filtering has not been implemented yet",
        ),
        SkipThisTest(
            name="test_PUT_404_nonexistent",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_400",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_PUT_422",
            details="Framework issue: AbstractEPTest doesn't use Update model fields properly",
        ),
        SkipThisTest(
            name="test_GQL_mutation_create",
            details="GraphQL mutation needs all required fields in test",
        ),
        SkipThisTest(
            name="test_GQL_mutation_update",
            details="GraphQL mutation needs all required fields in test",
        ),
    ]

    # An artifact's parent is its message; the server files it in the
    # message's conversation (naming another conversation is refused).
    parent_entities = [
        ParentEntity(
            name="message",
            foreign_key="message_id",
            nullable=False,
            path_level=None,  # Not in path
            test_class=TestMessageEndpoints,
        ),
    ]

    # NESTING_CONFIG_OVERRIDES = {
    #     "LIST": 1,  # /v1/conversation/{conv_id}/artifact
    #     "CREATE": 1,  # /v1/conversation/{conv_id}/artifact
    #     "DETAIL": 0,  # /v1/artifact/{id}
    #     "SEARCH": 0,  # /v1/artifact/search
    # }

    def create_payload(
        self,
        name: Optional[str] = None,
        parent_ids: Optional[Dict[str, str]] = None,
        team_id: Optional[str] = None,
        minimal: bool = False,
        invalid_data: bool = False,
    ) -> Dict[str, Any]:
        """Create a payload for artifact creation within conversation context.

        Args:
            name: Optional name for the artifact
            parent_ids: Optional parent IDs (conversation_id required, message_id optional)
            team_id: Optional team ID
            minimal: Whether to create a minimal payload
            invalid_data: Whether to create invalid data

        Returns:
            Dict containing artifact creation payload
        """
        name = name or f"Test Artifact {faker.catch_phrase()}"
        rel_path = f"test_artifacts/{name.replace(' ', '_').lower()}.txt"

        if invalid_data:
            # Create invalid data for testing validation
            payload = {
                "name": 12345,  # Invalid: number instead of string
                "relative_path": None,  # Invalid: null for required field
                "encrypted": "yes",  # Invalid: string instead of boolean
            }
        elif minimal:
            # Only include required fields
            payload = {
                "name": name,
                "relative_path": rel_path,
                "hosted_path": f"/static/{rel_path}",
                "content": f"Minimal content for {name}",
            }
        else:
            # Include all fields
            payload = {
                "name": name,
                "relative_path": rel_path,
                "hosted_path": f"/static/{rel_path}",
                "content": f"Test content for {name}: {faker.text()}",
                "encrypted": False,
            }

        # Add parent IDs if provided
        if parent_ids:
            if "conversation_id" in parent_ids:
                payload["conversation_id"] = (
                    parent_ids["conversation_id"].id
                    if hasattr(parent_ids["conversation_id"], "id")
                    else parent_ids["conversation_id"]
                )
            if "message_id" in parent_ids:
                payload["message_id"] = (
                    parent_ids["message_id"].id
                    if hasattr(parent_ids["message_id"], "id")
                    else parent_ids["message_id"]
                )

        return payload

    def test_GET_200_encrypted_artifacts(
        self, server: Any, admin_a: Any, team_a: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Test retrieving encrypted artifacts.

        Args:
            server: Test client instance
            admin_a: Admin user object with jwt property
            team_a: Team context

        Returns:
            List of encrypted artifacts
        """
        if self.reason_to_skip("test_GET_200_encrypted_artifacts"):
            pytest.skip("Skipping test_GET_200_encrypted_artifacts")

        # First create an encrypted artifact
        encrypted_payload = self.create_payload()
        encrypted_payload["encrypted"] = True
        encrypted_payload["name"] = f"Encrypted Artifact {uuid.uuid4()}"

        # Create parent entities
        conversation_test = TestConversationEndpoints()
        conversation = conversation_test.test_POST_201(server, admin_a, team_a)

        response = server.post(
            f"/v1/conversation/{conversation['id']}/artifact",
            json=encrypted_payload,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        self._assert_response_status(response, 201, "POST encrypted artifact", "")

        # Get only encrypted artifacts
        endpoint = f"/v1/artifact?encrypted=true"
        response = server.get(
            endpoint,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        self._assert_response_status(response, 200, "GET encrypted artifacts", endpoint)
        json_response = response.json()

        if "artifacts" not in json_response and "artifact" not in json_response:
            raise AssertionError(
                f"[{self.entity_name}] Artifact list not found in response\n"
                f"Response: {json_response}"
            )

        artifacts = json_response.get("artifacts", json_response.get("artifact", []))

        # Verify all returned artifacts are encrypted
        for artifact in artifacts:
            if not artifact.get("encrypted", False):
                raise AssertionError(
                    f"[{self.entity_name}] Non-encrypted artifact returned\n"
                    f"Artifact: {artifact}"
                )

        return artifacts
