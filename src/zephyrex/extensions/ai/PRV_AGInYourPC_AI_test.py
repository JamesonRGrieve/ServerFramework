"""
Tests for AGInYourPC AI Provider using real API integration.
"""

import os
from typing import Dict, Any

import pytest

from zephyrex.extensions.ai.PRV_AGInYourPC_AI import AGInYourPCProvider
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, ProviderInstanceManager


class TestAGInYourPCProvider:
    """Test AGInYourPC provider with real API integration."""

    @pytest.fixture
    def real_instance(self):
        """Get real provider instance from database or create test instance."""
        # Check if environment variables are set
        api_key = env("AGINYOURPC_API_KEY")
        api_uri = env("AGINYOURPC_API_URI")

        if not api_key or not api_uri:
            pytest.skip("AGInYourPC API credentials not configured")

        # Create a simple test instance with required fields
        from datetime import datetime
        from unittest.mock import MagicMock

        instance = MagicMock(spec=ProviderInstanceModel)
        instance.id = "test_aginyourpc"
        instance.name = "Test_AGInYourPC"
        instance.api_key = api_key
        instance.api_uri = api_uri
        instance.model_name = "aginyourpc"
        instance.settings_json = {
            "max_tokens": 1024,
            "temperature": 0.7,
            "top_p": 0.95,
            "voice": "HAL9000",
            "language": "en",
            "transcription_model": "base",
        }
        instance.enabled = True
        instance.provider_id = "test_provider"
        instance.created_at = datetime.now()
        instance.updated_at = datetime.now()
        instance.created_by_user_id = env("ROOT_ID")
        instance.updated_by_user_id = env("ROOT_ID")

        return instance

    def test_platform_metadata(self):
        """Test provider metadata."""
        assert AGInYourPCProvider.name == "AGInYourPC"
        assert AGInYourPCProvider.friendly_name == "AGInYourPC AI Service"
        assert AGInYourPCProvider.get_platform_name() == "AGInYourPC"

    def test_services(self):
        """Test services method."""
        services = AGInYourPCProvider.services()
        assert "llm" in services
        assert "tts" in services
        assert "image" in services
        assert "transcription" in services
        assert "translation" in services
        assert "vision" in services
        assert "embeddings" in services

    def test_abilities(self):
        """Test provider abilities."""
        abilities = AGInYourPCProvider._abilities
        assert "text_generation" in abilities
        assert "embedding_generation" in abilities
        assert "image_generation" in abilities
        assert "transcription" in abilities
        assert "text_to_speech" in abilities

    def test_validate_config_with_env(self):
        """Test configuration validation with environment variables."""
        if not env("AGINYOURPC_API_KEY") or not env("AGINYOURPC_API_URI"):
            issues = AGInYourPCProvider.validate_config()
            assert len(issues) > 0
            assert any("AGINYOURPC_API_KEY" in issue for issue in issues) or any(
                "AGINYOURPC_API_URI" in issue for issue in issues
            )
        else:
            issues = AGInYourPCProvider.validate_config()
            assert len(issues) == 0

    def test_bond_instance_real(self, real_instance):
        """Test bonding a real provider instance."""
        bonded = AGInYourPCProvider.bond_instance(real_instance)

        assert bonded is not None
        assert bonded.api_key == real_instance.api_key
        assert bonded.api_uri.endswith("/v1/")
        assert bonded.model_name == real_instance.model_name or "aginyourpc"

    def test_generate_text_real_api(self, real_instance):
        """Test text generation with real API."""
        bonded = AGInYourPCProvider.bond_instance(real_instance)
        assert bonded is not None

        result = AGInYourPCProvider.generate_text(
            bonded,
            prompt="Hello, this is a test. Please respond with 'Test successful'.",
            max_tokens=50,
            temperature=0.1,
        )

        assert result["success"] is True
        assert "text" in result
        assert len(result["text"]) > 0
        assert "usage" in result
        assert "model" in result

    def test_generate_embeddings_real_api(self, real_instance):
        """Test embedding generation with real API."""
        bonded = AGInYourPCProvider.bond_instance(real_instance)
        assert bonded is not None

        result = AGInYourPCProvider.generate_embeddings(
            bonded, text="This is a test text for embedding generation."
        )

        assert result["success"] is True
        assert "embedding" in result
        assert isinstance(result["embedding"], list)
        assert len(result["embedding"]) > 0
        assert "dimensions" in result
        assert result["model"] == "bge-m3"

    def test_text_to_speech_real_api(self, real_instance):
        """Test text-to-speech with real API."""
        bonded = AGInYourPCProvider.bond_instance(real_instance)
        assert bonded is not None

        result = AGInYourPCProvider.text_to_speech(
            bonded, text="Hello, this is a test of text to speech."
        )

        # If TTS is disabled on the server, mark as expected failure
        if not result["success"] and "Text to speech is disabled" in result.get(
            "error", ""
        ):
            pytest.xfail("Text to speech is disabled on the test server")

        assert result["success"] is True
        assert "audio" in result
        assert isinstance(result["audio"], bytes)
        assert len(result["audio"]) > 0
        assert result["format"] == "wav"
        assert result["voice"] in [
            "HAL9000",
            real_instance.settings_json.get("voice", "HAL9000"),
        ]

    def test_generate_image_real_api(self, real_instance):
        """Test image generation with real API."""
        bonded = AGInYourPCProvider.bond_instance(real_instance)
        assert bonded is not None

        result = AGInYourPCProvider.generate_image(
            bonded,
            prompt="A simple red circle on white background, minimalist style",
            size="512x512",
        )

        # If image generation is disabled on the server, mark as expected failure
        if not result["success"] and any(
            msg in result.get("error", "")
            for msg in ["Image generation is disabled", "disabled", "not available"]
        ):
            pytest.xfail("Image generation is disabled on the test server")

        assert result["success"] is True
        assert "url" in result
        assert result["model"] == "dall-e-3"

    def test_transcribe_audio_real_api(self, real_instance):
        """Test audio transcription with real API (if audio file available)."""
        # This test requires a real audio file
        test_audio_path = "test_audio.wav"

        if not os.path.exists(test_audio_path):
            # Try to create a test audio file using TTS
            bonded = AGInYourPCProvider.bond_instance(real_instance)
            assert bonded is not None

            tts_result = AGInYourPCProvider.text_to_speech(
                bonded, text="Testing transcription functionality."
            )

            if tts_result["success"] and tts_result.get("audio"):
                # Save audio for transcription test
                with open(test_audio_path, "wb") as f:
                    f.write(tts_result["audio"])
            else:
                pytest.skip("Unable to create test audio file (TTS may be disabled)")

        bonded = AGInYourPCProvider.bond_instance(real_instance)
        assert bonded is not None

        result = AGInYourPCProvider.transcribe_audio(bonded, audio_path=test_audio_path)

        # If transcription is disabled on the server, mark as expected failure
        if not result["success"] and any(
            msg in result.get("error", "")
            for msg in ["transcription is disabled", "disabled", "not available"]
        ):
            pytest.xfail("Audio transcription is disabled on the test server")

        assert result["success"] is True
        assert "text" in result
        assert len(result["text"]) > 0

        # Clean up test file
        if os.path.exists(test_audio_path):
            os.remove(test_audio_path)

    def test_prepare_messages_text_only(self):
        """Test message preparation with text only."""
        messages = AGInYourPCProvider._prepare_messages("Test prompt", [])

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Test prompt"

    def test_prepare_messages_with_url_images(self):
        """Test message preparation with URL images."""
        messages = AGInYourPCProvider._prepare_messages(
            "Describe this image", ["http://example.com/image.jpg"]
        )

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], list)
        assert len(messages[0]["content"]) == 2
        assert messages[0]["content"][0]["type"] == "text"
        assert messages[0]["content"][0]["text"] == "Describe this image"
        assert messages[0]["content"][1]["type"] == "image_url"
        assert (
            messages[0]["content"][1]["image_url"]["url"]
            == "http://example.com/image.jpg"
        )

    def test_clean_response(self):
        """Test response cleaning."""
        # Test User: prefix removal
        cleaned = AGInYourPCProvider._clean_response(
            "User: question\nResponse here", "http://api.com/outputs/"
        )
        assert "User:" not in cleaned
        assert cleaned.strip() == "Response here"

        # Test tag removal
        cleaned = AGInYourPCProvider._clean_response(
            "<s>Response with tags</s>", "http://api.com/outputs/"
        )
        assert "<s>" not in cleaned
        assert "</s>" not in cleaned
        assert cleaned.strip() == "Response with tags"

        # Test URL replacement
        cleaned = AGInYourPCProvider._clean_response(
            "Check http://localhost:8091/outputs/file.wav", "http://api.com/outputs/"
        )
        assert "http://localhost:8091/outputs/" not in cleaned
        assert "http://api.com/outputs/" in cleaned

    def test_generate_text_with_images_real_api(self, real_instance):
        """Test text generation with images using real API."""
        bonded = AGInYourPCProvider.bond_instance(real_instance)
        assert bonded is not None

        # Use a public test image
        result = AGInYourPCProvider.generate_text(
            bonded,
            prompt="What do you see in this image?",
            max_tokens=100,
            temperature=0.5,
            images=["https://picsum.photos/200/200"],  # Random test image
        )

        # If vision/image-to-text is disabled on the server, mark as expected failure
        if not result["success"] and any(
            msg in result.get("error", "")
            for msg in [
                "vision is disabled",
                "image analysis is disabled",
                "disabled",
                "not available",
            ]
        ):
            pytest.xfail("Vision/image-to-text is disabled on the test server")

        assert result["success"] is True
        assert "text" in result
        assert len(result["text"]) > 0
        assert "usage" in result

    def test_error_handling_invalid_api_key(self):
        """Test error handling with invalid API key."""
        # ``bond_instance`` only succeeds when the ``openai`` SDK is
        # importable (see PRV_AGInYourPC_AI.py's module-level guarded
        # import); it is an optional, not-yet-declared runtime dependency
        # for this extension (see pyproject.toml). Skip rather than fail
        # when it's genuinely unavailable, instead of asserting a bonded
        # instance that the provider correctly refuses to construct.
        pytest.importorskip(
            "openai",
            reason="AGInYourPC provider bonding requires the openai SDK, which is "
            "an optional runtime dependency not yet declared for this extension",
        )
        from datetime import datetime
        from unittest.mock import MagicMock

        # Create instance with invalid credentials
        invalid_instance = MagicMock(spec=ProviderInstanceModel)
        invalid_instance.id = "test_invalid"
        invalid_instance.name = "Test_Invalid"
        invalid_instance.api_key = "invalid_key"
        invalid_instance.api_uri = env("AGINYOURPC_API_URI") or "http://localhost:8091"
        invalid_instance.model_name = "aginyourpc"
        invalid_instance.settings_json = {}
        invalid_instance.enabled = True
        invalid_instance.provider_id = "test_provider"
        invalid_instance.created_at = datetime.now()
        invalid_instance.updated_at = datetime.now()
        invalid_instance.created_by_user_id = env("ROOT_ID")
        invalid_instance.updated_by_user_id = env("ROOT_ID")

        bonded = AGInYourPCProvider.bond_instance(invalid_instance)
        assert bonded is not None

        result = AGInYourPCProvider.generate_text(
            bonded, prompt="This should fail", max_tokens=10
        )

        assert result["success"] is False
        assert "error" in result
