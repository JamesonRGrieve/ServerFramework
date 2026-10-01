"""Test suite for DB_Conversations module."""

from uuid import uuid4

from faker import Faker

from AbstractTest import CategoryOfTest, ClassOfTestsConfig, ParentEntity
from zephyrex.database.AbstractDBTest import AbstractDBTest
from zephyrex.database.DB_Auth_test import TestUser as CoreUserTests
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.conversations.BLL_Conversations import (
    ArtifactModel,
    ConversationModel,
    ConversationUserModel,
    FeedbackModel,
    MessageModel,
)
from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations

# Set default test configuration for all test classes
AbstractDBTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.DATABASE, CategoryOfTest.EXTENSION]
)

faker = Faker()


class TestConversation(AbstractDBTest, ExtensionServerMixin):
    class_under_test = ConversationModel
    extension_class = EXT_Conversations
    create_fields = {
        "name": lambda: f"Test Conversation {uuid4()}",
        "description": faker.text,
    }
    update_fields = {
        "name": "Updated Conversation",
        "description": "Updated conversation description",
    }
    unique_field = "name"
    parent_entities = [
        ParentEntity(name="user", foreign_key="user_id", test_class=CoreUserTests)
    ]


class TestConversationUser(AbstractDBTest, ExtensionServerMixin):
    class_under_test = ConversationUserModel
    extension_class = EXT_Conversations
    create_fields = {
        "user_id": None,
        "conversation_id": None,
    }
    update_fields = {}
    parent_entities = [
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=TestConversation,
        ),
        ParentEntity(name="user", foreign_key="user_id", test_class=CoreUserTests),
    ]


class TestMessage(AbstractDBTest, ExtensionServerMixin):
    class_under_test = MessageModel
    extension_class = EXT_Conversations
    create_fields = {
        "content": faker.text,
    }
    update_fields = {
        "content": "Updated message content",
    }
    parent_entities = [
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=TestConversation,
        ),
        ParentEntity(name="user", foreign_key="user_id", test_class=CoreUserTests),
    ]


class TestArtifact(AbstractDBTest, ExtensionServerMixin):
    class_under_test = ArtifactModel
    extension_class = EXT_Conversations
    create_fields = {
        "name": lambda: f"test_artifact_{uuid4()}.txt",
        "relative_path": lambda: f"/files/test_artifact_{uuid4()}.txt",
        "hosted_path": lambda: f"https://storage.example.com/test_artifact_{uuid4()}.txt",
        "content": faker.text,
        "encrypted": False,
    }
    update_fields = {
        "name": "updated_artifact.txt",
        "content": "Updated artifact content",
        "encrypted": True,
    }
    parent_entities = [
        ParentEntity(
            name="conversation",
            foreign_key="conversation_id",
            test_class=TestConversation,
        ),
        ParentEntity(
            name="message",
            foreign_key="message_id",
            test_class=TestMessage,
        ),
    ]


class TestFeedback(AbstractDBTest, ExtensionServerMixin):
    class_under_test = FeedbackModel
    extension_class = EXT_Conversations
    create_fields = {
        "content": faker.text,
        "positive": True,
    }
    update_fields = {
        "content": "Updated feedback content",
        "positive": False,
    }
    parent_entities = [
        ParentEntity(
            name="message",
            foreign_key="message_id",
            test_class=TestMessage,
        ),
    ]
