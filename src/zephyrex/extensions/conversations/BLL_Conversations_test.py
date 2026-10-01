from faker import Faker

from AbstractTest import ParentEntity
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.conversations.BLL_Conversations import (
    ArtifactManager,
    ConversationManager,
    ConversationUserManager,
    FeedbackManager,
    MessageManager,
)
from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest, CategoryOfTest, ClassOfTestsConfig
from zephyrex.logic.BLL_Auth_test import TestUserManager as CoreUserManagerTests

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.LOGIC, CategoryOfTest.EXTENSION]
)

# Initialize faker for generating test data once
faker = Faker()


class TestConversationManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ConversationManager
    extension_class = EXT_Conversations
    create_fields = {
        "name": lambda: f"Test Conversation {faker.word()}",
        "description": lambda: faker.sentence(),
    }
    update_fields = {
        "name": "Updated Conversation",
        "description": "Updated conversation description",
    }
    unique_fields = ["name"]
    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserManagerTests,
        ),
    ]

    # def test_create_group_conversation(self, admin_a, model_registry):
    #     """Test creating a group conversation"""
    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         conversation = manager.create(
    #             user_id=admin_a.id,
    #             name=f"Group Chat {faker.word()}",
    #             description="A group conversation",
    #             is_group_chat=True,
    #         )

    #         assert conversation is not None
    #         assert conversation.is_group_chat is True
    #         assert conversation.user_id == admin_a.id

    # def test_conversation_with_participants(self, admin_a, model_registry):
    #     """Test managing conversation participants"""
    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create a group conversation
    #         conversation = manager.create(
    #             user_id=admin_a.id,
    #             name=f"Team Chat {faker.word()}",
    #             description="Team collaboration chat",
    #             is_group_chat=True,
    #         )

    #         # Add participants using nested manager
    #         manager.conversation_users.create(
    #             conversation_id=conversation.id,
    #             user_id=admin_a.id,
    #             role="admin",
    #             is_active=True,
    #         )

    #         # List participants
    #         participants = manager.conversation_users.list(
    #             conversation_id=conversation.id
    #         )
    #         assert len(participants) >= 1

    # def test_get_conversation_with_messages(self, admin_a, model_registry):
    #     """Test getting a conversation with its messages"""
    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create a conversation
    #         conversation = manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="A conversation with messages",
    #             is_group_chat=False,
    #         )

    #         # Add messages using the nested manager
    #         manager.messages.create(
    #             conversation_id=conversation.id,
    #             content="Test message 1",
    #         )
    #         manager.messages.create(
    #             conversation_id=conversation.id,
    #             content="Test message 2",
    #         )

    #         # Get the conversation
    #         result = manager.get(id=conversation.id)

    #         # Verify conversation was retrieved
    #         assert result.id == conversation.id
    #         assert result.name == conversation.name

    #         # Get messages separately
    #         messages = manager.messages.list(conversation_id=conversation.id)
    #         assert len(messages) == 2


class TestConversationUserManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ConversationUserManager
    extension_class = EXT_Conversations
    create_fields = {
        "conversation_id": None,
        "user_id": None,
    }
    update_fields = {}
    parent_entities = [
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=TestConversationManager,
        ),
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserManagerTests,
        ),
    ]

    # def test_participant_roles(self, admin_a, model_registry):
    #     """Test different participant roles in conversations"""
    #     # Create a conversation first
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Role Test Chat {faker.word()}",
    #             description="Testing participant roles",
    #             is_group_chat=True,
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Test admin role
    #         admin_participant = manager.create(
    #             conversation_id=conversation.id,
    #             user_id=admin_a.id,
    #             role="admin",
    #             is_active=True,
    #         )
    #         assert admin_participant.role == "admin"

    #         # Test moderator role
    #         moderator_participant = manager.create(
    #             conversation_id=conversation.id,
    #             user_id=admin_a.id,
    #             role="moderator",
    #             is_active=True,
    #         )
    #         assert moderator_participant.role == "moderator"

    #         # Test participant role
    #         regular_participant = manager.create(
    #             conversation_id=conversation.id,
    #             user_id=admin_a.id,
    #             role="participant",
    #             is_active=True,
    #         )
    #         assert regular_participant.role == "participant"

    # def test_mute_participant(self, admin_a, model_registry):
    #     """Test muting and unmuting participants"""
    #     # Create a conversation
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Mute Test Chat {faker.word()}",
    #             description="Testing participant muting",
    #             is_group_chat=True,
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create participant
    #         participant = manager.create(
    #             conversation_id=conversation.id,
    #             user_id=admin_a.id,
    #             role="participant",
    #             is_active=True,
    #             is_muted=False,
    #         )
    #         assert participant.is_muted is False

    #         # Mute the participant
    #         updated_participant = manager.update(
    #             id=participant.id,
    #             is_muted=True,
    #         )
    #         assert updated_participant.is_muted is True


class TestMessageManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = MessageManager
    extension_class = EXT_Conversations
    create_fields = {
        "content": lambda: f"Test message: {faker.sentence()}",
    }
    update_fields = {
        "content": "Updated message content",
    }
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=TestConversationManager,
        ),
    ]

    # def test_edit_message_with_forking(self, admin_a, model_registry):
    #     """Test editing a message with and without forking"""
    #     # Create a conversation first
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="A conversation for message editing",
    #             is_group_chat=False,
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create a message
    #         message = manager.create(
    #             conversation_id=conversation.id,
    #             content="Original message content",
    #         )

    #         # Test edit without forking
    #         updated_message = MessageManager.edit(
    #             manager=manager, id=message.id, content="Edited content", fork=False
    #         )
    #         assert updated_message.id == message.id
    #         assert updated_message.content == "Edited content"

    #         # Test edit with forking
    #         forked_message = MessageManager.edit(
    #             manager=manager, id=message.id, content="Forked content", fork=True
    #         )
    #         assert forked_message.id != message.id
    #         assert forked_message.content == "Forked content"
    #         assert forked_message.conversation_id == conversation.id

    # def test_message_ordering(self, admin_a, model_registry):
    #     """Test message ordering in conversations"""
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="Testing message ordering",
    #             is_group_chat=False,
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create multiple messages
    #         messages = []
    #         for i in range(5):
    #             message = manager.create(
    #                 conversation_id=conversation.id,
    #                 content=f"Message {i}",
    #             )
    #             messages.append(message)

    #         # List messages and verify ordering
    #         message_list = manager.list(conversation_id=conversation.id)
    #         assert len(message_list) == 5
    #         # Verify messages are in order (newest first or oldest first depending on implementation)


class TestFeedbackManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = FeedbackManager
    extension_class = EXT_Conversations
    create_fields = {
        "content": lambda: f"Feedback: {faker.sentence()}",
        "positive": True,
    }
    update_fields = {
        "content": "Updated feedback content",
        "positive": False,
    }
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="message", foreign_key="message_id", test_class=TestMessageManager
        ),
    ]

    # def test_feedback_positive_attribute(self, admin_a, model_registry):
    #     """Test creating feedback with positive/negative attributes"""
    #     # Create a conversation and message first
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="A conversation for feedback testing",
    #             is_group_chat=False,
    #         )

    #         # Create a message using nested manager
    #         message = conversation_manager.messages.create(
    #             conversation_id=conversation.id,
    #             content="AI response to provide feedback on",
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Test positive feedback
    #         positive_feedback = manager.create(
    #             message_id=message.id,
    #             content="This response was helpful!",
    #             positive=True,
    #         )
    #         assert positive_feedback.positive is True

    #         # Test negative feedback
    #         negative_feedback = manager.create(
    #             message_id=message.id,
    #             content="This response wasn't helpful.",
    #             positive=False,
    #         )
    #         assert negative_feedback.positive is False

    #         # Test neutral feedback
    #         neutral_feedback = manager.create(
    #             message_id=message.id,
    #             content="Just noting this for reference.",
    #             positive=None,
    #         )
    #         assert neutral_feedback.positive is None

    # def test_multiple_feedback_per_message(self, admin_a, model_registry):
    #     """Test multiple feedbacks on a single message"""
    #     # Create conversation and message
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="Testing multiple feedback",
    #             is_group_chat=False,
    #         )

    #         message = conversation_manager.messages.create(
    #             conversation_id=conversation.id,
    #             content="AI response for multiple feedback",
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create multiple feedbacks
    #         feedbacks = []
    #         for i in range(3):
    #             feedback = manager.create(
    #                 message_id=message.id,
    #                 content=f"Feedback {i}",
    #                 positive=i % 2 == 0,  # Alternating positive/negative
    #             )
    #             feedbacks.append(feedback)

    #         # List feedbacks for the message
    #         feedback_list = manager.list(message_id=message.id)
    #         assert len(feedback_list) >= 3


class TestArtifactManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = ArtifactManager
    extension_class = EXT_Conversations
    create_fields = {
        "name": lambda: f"Test Artifact {faker.word()}",
        "relative_path": lambda: f"test/path/{faker.file_name()}",
        "hosted_path": lambda: f"https://example.com/artifacts/{faker.file_name()}",
        "content": lambda: faker.text(),
        "encrypted": False,
    }
    update_fields = {
        "name": f"Updated Artifact {faker.word()}",
        "relative_path": "test/path/updated_artifact.txt",
        "hosted_path": "https://example.com/artifacts/updated_artifact.txt",
    }
    unique_fields = []

    parent_entities = [
        ParentEntity(
            name="user",
            foreign_key="user_id",
            test_class=CoreUserManagerTests,
        ),
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=TestConversationManager,
        ),
    ]

    # def test_artifact_validation(self, admin_a, model_registry):
    #     """Test artifact creation validation"""
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="A conversation for artifact testing",
    #             is_group_chat=False,
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Test creating artifact without conversation_id should fail
    #         from pydantic import ValidationError

    #         with pytest.raises(ValidationError) as exc_info:
    #             manager.create(
    #                 name="Invalid Artifact",
    #                 relative_path="test/path/invalid.txt",
    #                 hosted_path="https://example.com/artifacts/invalid.txt",
    #             )
    #         assert "Either conversation_id or message_id must be provided" in str(
    #             exc_info.value
    #         )

    #         # Test creating artifact with conversation_id should succeed
    #         artifact = manager.create(
    #             name="Valid Artifact",
    #             relative_path="test/path/valid.txt",
    #             hosted_path="https://example.com/artifacts/valid.txt",
    #             conversation_id=conversation.id,
    #             encrypted=False,
    #         )
    #         assert artifact.conversation_id == conversation.id
    #         assert artifact.name == "Valid Artifact"

    # def test_artifact_message_association(self, admin_a, model_registry):
    #     """Test artifact association with messages"""
    #     # Create a conversation and message first
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="A conversation for artifact message association",
    #             is_group_chat=False,
    #         )

    #         # Create message using nested manager
    #         message = conversation_manager.messages.create(
    #             conversation_id=conversation.id,
    #             content="Message with an artifact attachment",
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create an artifact associated with both conversation and message
    #         artifact = manager.create(
    #             name="Message Artifact",
    #             relative_path="test/path/message_artifact.txt",
    #             hosted_path="https://example.com/artifacts/message_artifact.txt",
    #             conversation_id=conversation.id,
    #             message_id=message.id,
    #             content="Artifact content",
    #             encrypted=False,
    #         )

    #         # Verify associations
    #         assert artifact.conversation_id == conversation.id
    #         assert artifact.message_id == message.id
    #         assert artifact.content == "Artifact content"

    # def test_artifact_encryption(self, admin_a, model_registry):
    #     """Test artifact encryption functionality"""
    #     with ConversationManager(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as conversation_manager:
    #         conversation = conversation_manager.create(
    #             user_id=admin_a.id,
    #             name=f"Test Conversation {faker.word()}",
    #             description="Testing artifact encryption",
    #             is_group_chat=False,
    #         )

    #     with self.class_under_test(
    #         requester_id=admin_a.id, model_registry=model_registry
    #     ) as manager:
    #         # Create encrypted artifact
    #         encrypted_artifact = manager.create(
    #             name="Encrypted Artifact",
    #             relative_path="test/path/encrypted.txt",
    #             hosted_path="https://example.com/artifacts/encrypted.txt",
    #             conversation_id=conversation.id,
    #             content="Sensitive content",
    #             encrypted=True,
    #         )

    #         assert encrypted_artifact.encrypted is True

    #         # Create unencrypted artifact
    #         unencrypted_artifact = manager.create(
    #             name="Unencrypted Artifact",
    #             relative_path="test/path/unencrypted.txt",
    #             hosted_path="https://example.com/artifacts/unencrypted.txt",
    #             conversation_id=conversation.id,
    #             content="Public content",
    #             encrypted=False,
    #         )

    #         assert unencrypted_artifact.encrypted is False
