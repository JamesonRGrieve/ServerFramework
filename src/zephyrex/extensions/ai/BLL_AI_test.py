import os
import re
from datetime import datetime

import pytest
from faker import Faker

from AbstractTest import (
    CategoryOfTest,
    ClassOfTestsConfig,
    ParentEntity,
    SkipReason,
    SkipThisTest,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.extensions.ai.BLL_AI import AiRequestManager, AiStatisticsManager
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest

# Set default test configuration for all test classes
AbstractBLLTest.test_config = ClassOfTestsConfig(categories=[CategoryOfTest.LOGIC])

faker = Faker()


def _get_test_dependency_name():
    """Generate a dependency name from the current test file name."""
    filename = os.path.basename(__file__)
    name = re.sub(r"\.py$", "", filename).lower()
    name = re.sub(r"_test$", "_tests", name)
    if not name.endswith("_tests"):
        name += "_tests"
    return name


class TestAiRequestManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = AiRequestManager
    extension_class = EXT_AI

    @pytest.fixture(scope="module")
    def server(self):
        """Isolated test server that also loads ``conversations``.

        ``test_transcribe_audio_to_test`` below exercises the real
        ``conversations`` extension's ``ConversationManager``/
        ``MessageManager`` (the audio-transcription hook lives there and
        calls back into ``zephyrex.extensions.ai.BLL_AI.transcribe_audio_to_text``).
        The default ``ExtensionServerMixin.server`` only loads
        ``extension_class`` plus the framework's core-companion set, so
        ``conversations`` — a real ``EXT_Dependency`` of ``ai`` — must be
        added explicitly here for its models to be bound in this module's
        isolated registry.
        """
        if not self.extension_class:
            pytest.skip("extension_class not defined, test cannot run")

        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()

        from fastapi.testclient import TestClient

        extension_name = self.extension_class.name.lower()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        test_db_prefix = (
            f"test.{extension_name}.{worker_id}"
            if worker_id
            else f"test.{extension_name}"
        )
        from conftest import CORE_COMPANION_EXTENSIONS

        names = [extension_name, "conversations"] + [
            c
            for c in CORE_COMPANION_EXTENSIONS
            if c not in (extension_name, "conversations")
        ]
        extension_list = ",".join(names)
        try:
            from zephyrex.app import instance

            app = instance(db_prefix=test_db_prefix, extensions=extension_list)
            client = TestClient(app)
            yield client
        except ImportError as e:
            pytest.skip(f"FastAPI dependencies not available: {e}")
        except Exception as e:
            pytest.skip(f"Server setup failed: {e}")

    create_fields = {
        "name": lambda: f"ai_request_{faker.unique.uuid4()}",
        "prompt": faker.text,
        "provider_instance_id": lambda: faker.uuid4(),
        "request_type": lambda: faker.random_element(
            ["text_generation", "embedding", "image_generation"]
        ),
        "model_name": lambda: faker.random_element(
            ["gpt-4", "gpt-3.5-turbo", "dall-e-3"]
        ),
    }
    update_fields = {
        "response": "Updated AI response",
        "status": "completed",
        "tokens_used": 200,
        "cost": 0.50,
    }
    unique_fields = ["name"]

    def test_create(self, admin_a, team_a, server, model_registry):
        """Test creating an AI request."""
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id, team_a.id, server=server, model_registry=model_registry
        )
        self._create_assert("create")

    # test_update is inherited from AbstractBLLTest, which correctly seeds
    # the tracked "update" entity via _create() before calling _update().

    async def test_generate_text(self, admin_a, team_a, server, model_registry):
        """Test the generate_text method."""
        manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )

        result = await manager.generate_text(
            prompt="What is artificial intelligence?",
            provider_instance_id="test-provider-instance",
            model_name="gpt-4",
        )

        assert "request_id" in result
        assert result["status"] == "pending"

    async def test_create_embedding(self, admin_a, team_a, server, model_registry):
        """Test the create_embedding method."""
        manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )

        result = await manager.create_embedding(
            text="Sample text for embedding",
            provider_instance_id="test-provider-instance",
        )

        assert "request_id" in result
        assert result["status"] == "pending"

    async def test_generate_image(self, admin_a, team_a, server, model_registry):
        """Test the generate_image method."""
        manager = self.class_under_test(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            model_registry=model_registry,
        )

        result = await manager.generate_image(
            prompt="A beautiful landscape",
            provider_instance_id="test-provider-instance",
        )

        assert "request_id" in result
        assert result["status"] == "pending"

    def test_transcribe_audio_to_test(self, admin_a, model_registry):
        # This exercises a real end-to-end transcription round trip through
        # the AGInYourPC provider (the only ai-extension provider with a
        # "transcription" ability — see PRV_AGInYourPC_AI.py). That provider
        # only bonds when the ``openai`` SDK is importable (it is an
        # optional, not-yet-declared runtime dependency for this extension —
        # see pyproject.toml's note on the domain extensions' third-party
        # surface) and needs a reachable AGInYourPC server. Skip gracefully
        # when either prerequisite is absent, mirroring the same convention
        # PRV_AGInYourPC_AI_test.py's ``real_instance`` fixture already uses
        # for this exact provider's other real-API tests.
        pytest.importorskip(
            "openai",
            reason="AGInYourPC provider bonding requires the openai SDK, which is "
            "an optional runtime dependency not yet declared for this extension",
        )
        from zephyrex.lib.Environment import env

        if not env("AGINYOURPC_API_KEY") or not env("AGINYOURPC_API_URI"):
            pytest.skip("AGInYourPC API credentials not configured")

        # Import required dependencies
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
            MessageManager,
        )

        # 1. Create a conversation
        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conv_manager:
            conversation = conv_manager.create(name=f"Test Conversation {faker.word()}")

        import os

        # Get the directory of the current file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Construct the path to the audio file
        audio_file_path = os.path.join(current_dir, "testfiles", "BLL_AI_test.mp3")

        with open(
            audio_file_path,
            "rb",
        ) as audio_file:
            audio_data = audio_file.read()

            import base64

            encoded_string = base64.b64encode(audio_data)
            base64_string = encoded_string.decode("utf-8")

        with MessageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as msg_manager:
            user_message = msg_manager.create(
                conversation_id=conversation.id,
                file=base64_string,
                user_id=admin_a.id,
            )

            all_messages = msg_manager.list(conversation_id=conversation.id)

            # The user message should exist
            assert len(all_messages) >= 1
            assert user_message.content == " Hello this is John, Feeling Fever Today."
            assert user_message.user_id == admin_a.id

        with MessageManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as msg_cleanup:
            msg_cleanup.delete(id=user_message.id)

        with ConversationManager(
            requester_id=admin_a.id, model_registry=model_registry
        ) as conv_cleanup:
            conv_cleanup.delete(id=conversation.id)


class TestAiStatisticsManager(AbstractBLLTest, ExtensionServerMixin):
    class_under_test = AiStatisticsManager
    extension_class = EXT_AI
    create_fields = {
        "date": lambda: faker.date_this_year(),
        "provider_name": lambda: faker.random_element(
            ["OpenAI", "Anthropic", "Google"]
        ),
        "model_name": lambda: faker.random_element(["gpt-4", "claude-3", "gemini-pro"]),
        "request_count": lambda: faker.random_int(min=1, max=1000),
        "tokens_used": lambda: faker.random_int(min=100, max=50000),
        "total_cost": lambda: faker.pyfloat(
            left_digits=3, right_digits=2, positive=True
        ),
    }
    update_fields = {
        "request_count": 500,
        "tokens_used": 25000,
        "success_rate": 0.95,
    }

    skip_tests = [
        SkipThisTest(
            name="test_batch_update",
            reason=SkipReason.IRRELEVANT,
            details="Statistics are typically managed as system entities",
        ),
        SkipThisTest(
            name="test_batch_delete",
            reason=SkipReason.IRRELEVANT,
            details="Statistics are typically managed as system entities",
        ),
    ]

    def test_create(self, admin_a, team_a, server, model_registry):
        """Test creating AI statistics."""
        self.server = server
        self.model_registry = model_registry
        self._create(
            admin_a.id, team_a.id, server=server, model_registry=model_registry
        )
        self._create_assert("create")

    # test_update is inherited from AbstractBLLTest, which correctly seeds
    # the tracked "update" entity via _create() before calling _update().
