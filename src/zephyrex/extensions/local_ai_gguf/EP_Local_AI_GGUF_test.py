# """
# Test suite for Local AI GGUF Endpoint Layer.
# Tests GGUF endpoint router and API functionality.
# """

# from unittest.mock import MagicMock, patch

# import pytest
# from fastapi import FastAPI
# from fastapi.testclient import TestClient

# from AbstractTest import CategoryOfTest, ClassOfTestsConfig
# from endpoints.AbstractEPTest import AbstractEndpointTest
# from extensions.local_ai_gguf.BLL_Local_AI_GGUF import GGUFModelConfigurationManager
# from extensions.local_ai_gguf.EXT_Local_AI_GGUF import EXT_Local_AI_GGUF


# @pytest.mark.local_ai
# @pytest.mark.gguf
# @pytest.mark.ep
# class TestEPLocalAIGGUF(AbstractEndpointTest):
#     """Test GGUF extension @static_route functionality."""

#     test_config = ClassOfTestsConfig(categories=[CategoryOfTest.ENDPOINT])

#     @pytest.fixture
#     def app(self):
#         """Create FastAPI app with GGUF extension for testing."""
#         app = FastAPI()
#         # Extension routes are automatically registered via @static_route
#         return app

#     @pytest.fixture
#     def client(self, app):
#         """Create test client."""
#         return TestClient(app)

#     @pytest.fixture
#     def manager(self):
#         """Mock GGUF configuration manager."""
#         return GGUFModelConfigurationManager()

#     @pytest.fixture
#     def mock_manager(self):
#         """Mock GGUF configuration manager."""
#         manager = MagicMock()
#         manager.requester.id = "test-user-123"
#         manager.get.return_value = MagicMock()
#         manager.create.return_value = MagicMock()
#         manager.update.return_value = MagicMock()
#         manager.delete.return_value = MagicMock()
#         manager.list.return_value = []
#         manager.search.return_value = []
#         return manager

#     @pytest.fixture
#     def mock_user(self):
#         """Mock authenticated user."""
#         user = MagicMock()
#         user.id = "test-user-123"
#         return user

#     def test_extension_configuration(self):
#         """Test GGUF extension configuration."""
#         assert EXT_Local_AI_GGUF.name == "local_ai_gguf"
#         assert EXT_Local_AI_GGUF.friendly_name == "GGUF Model Support"
#         assert hasattr(EXT_Local_AI_GGUF, "_abilities")

#     def test_manager_configuration(self):
#         """Test GGUF manager configuration."""
#         manager = GGUFModelConfigurationManager()
#         assert hasattr(manager, "Model")
#         assert hasattr(manager, "ReferenceModel")

#     def test_hardware_optimization_endpoint(self, client, mock_manager, mock_user):
#         """Test hardware optimization endpoint."""
#         # Mock hardware detection
#         with patch(
#             "extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection"
#         ) as mock_hw:
#             mock_hw.return_value = {
#                 "has_cuda": True,
#                 "available_vram_gb": 12.0,
#             }

#             request_data = {"base_config_id": "test-config-123"}

#             response = client.post("/gguf/configs/hardware-optimize", json=request_data)

#             assert response.status_code == 200
#             data = response.json()
#             assert data["success"] is True
#             assert "optimized_config" in data
#             assert "hardware_info" in data
#             assert "applied_optimizations" in data

#     def test_compatibility_check_endpoint(self, client, mock_manager, mock_user):
#         """Test configuration compatibility check endpoint."""
#         # Mock hardware detection
#         with patch(
#             "extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection"
#         ) as mock_hw:
#             mock_hw.return_value = {
#                 "has_cuda": True,
#                 "available_vram_gb": 12.0,
#             }

#             config_id = "test-config-123"

#             response = client.get(f"/gguf/configs/{config_id}/compatibility")

#             assert response.status_code == 200
#             data = response.json()
#             assert data["success"] is True
#             assert "compatible" in data
#             assert "issues" in data
#             assert "recommendations" in data
#             assert "estimated_vram_gb" in data

#     def test_load_model_endpoint(self, client, mock_manager, mock_user):
#         """Test model instance loading endpoint."""
#         with patch(
#             "extensions.local_ai_gguf.BLL_Local_AI_GGUF.GGUFModelConfigurationManager.load_model_instance"
#         ) as mock_load:
#             mock_load.return_value = {
#                 "success": True,
#                 "config_id": "test-config-123",
#                 "llama_config": {"model_path": "test.gguf"},
#                 "message": "Model loaded successfully",
#             }

#             config_id = "test-config-123"

#             response = client.post(f"/gguf/configs/{config_id}/load-model")

#             assert response.status_code == 200
#             data = response.json()
#             assert data["success"] is True
#             assert data["config_id"] == config_id
#             assert "llama_config" in data

#     @patch("extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.text_to_text")
#     def test_generate_text_endpoint(
#         self, mock_generate, client, mock_manager, mock_user
#     ):
#         """Test text generation endpoint."""
#         # Mock the extension static method
#         mock_generate.return_value = {
#             "success": True,
#             "text": "Generated GGUF response",
#             "model_name": "test.gguf",
#             "provider_instance_id": "test-config-123",
#             "priority_level": "standard",
#             "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
#         }

#         config_id = "test-config-123"
#         request_data = {
#             "prompt": "Hello, world!",
#             "max_tokens": 100,
#             "temperature": 0.7,
#             "use_beam_search": False,
#         }

#         response = client.post(
#             f"/gguf/configs/{config_id}/generate-text", json=request_data
#         )

#         assert response.status_code == 200
#         data = response.json()
#         assert "generated_text" in data
#         assert "usage" in data
#         assert "model_info" in data

#     @patch(
#         "extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.text_to_embedding"
#     )
#     def test_generate_embeddings_endpoint(
#         self, mock_generate, client, mock_manager, mock_user
#     ):
#         """Test embedding generation endpoint."""
#         # Mock the extension static method
#         mock_generate.return_value = {
#             "success": True,
#             "embeddings": [0.1] * 768,  # 768-dim embedding
#             "dimension": 768,
#             "provider_instance_id": "test-config-123",
#         }

#         config_id = "test-config-123"
#         request_data = {"text": "Text to embed", "normalize": True}

#         response = client.post(
#             f"/gguf/configs/{config_id}/generate-embeddings", json=request_data
#         )

#         assert response.status_code == 200
#         data = response.json()
#         assert len(data["embeddings"]) == 768
#         assert data["dimension"] == 768
#         assert "model_info" in data

#     @patch(
#         "extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.discover_gguf_models"
#     )
#     def test_discover_models_endpoint(
#         self, mock_discover, client, mock_manager, mock_user
#     ):
#         """Test model discovery endpoint."""
#         # Mock the extension static method
#         mock_discover.return_value = {
#             "success": True,
#             "discovered_models": [
#                 {"name": "model1.gguf", "size_gb": 4.2},
#                 {"name": "model2.gguf", "size_gb": 7.1},
#             ],
#             "count": 2,
#             "search_paths": ["/models"],
#         }

#         request_data = {"search_paths": ["/custom/models"]}

#         response = client.post("/gguf/configs/discover-models", json=request_data)

#         assert response.status_code == 200
#         data = response.json()
#         assert data["success"] is True
#         assert data["count"] == 2
#         assert len(data["discovered_models"]) == 2

#     @patch(
#         "extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.download_gguf_model"
#     )
#     def test_download_model_endpoint(
#         self, mock_download, client, mock_manager, mock_user
#     ):
#         """Test model download endpoint."""
#         # Mock the extension static method
#         mock_download.return_value = {
#             "success": True,
#             "download_id": "download-123",
#             "status": "queued",
#             "model_id": "microsoft/DialoGPT-medium",
#             "quantization": "Q4_K_M",
#             "estimated_size_gb": 4.2,
#             "message": "Download queued successfully",
#         }

#         request_data = {
#             "model_id": "microsoft/DialoGPT-medium",
#             "quantization": "Q4_K_M",
#         }

#         response = client.post("/gguf/configs/download-model", json=request_data)

#         assert response.status_code == 200
#         data = response.json()
#         assert data["success"] is True
#         assert data["download_id"] == "download-123"
#         assert data["model_id"] == "microsoft/DialoGPT-medium"
#         assert data["quantization"] == "Q4_K_M"

#     def test_text_generation_failure(self, client, mock_manager, mock_user):
#         """Test text generation endpoint failure handling."""
#         # Mock the extension static method to return failure
#         with patch(
#             "extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.text_to_text"
#         ) as mock_generate:
#             mock_generate.return_value = {"success": False, "error": "Model not loaded"}

#             config_id = "test-config-123"
#             request_data = {"prompt": "Hello, world!", "max_tokens": 100}

#             response = client.post(
#                 f"/gguf/configs/{config_id}/generate-text", json=request_data
#             )

#             assert response.status_code == 500

#     def test_embedding_generation_failure(self, client, mock_manager, mock_user):
#         """Test embedding generation endpoint failure handling."""
#         # Mock the extension static method to return failure
#         with patch(
#             "extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.text_to_embedding"
#         ) as mock_generate:
#             mock_generate.return_value = {
#                 "success": False,
#                 "error": "Embedding model not available",
#             }

#             config_id = "test-config-123"
#             request_data = {"text": "Text to embed", "normalize": True}

#             response = client.post(
#                 f"/gguf/configs/{config_id}/generate-embeddings", json=request_data
#             )

#             assert response.status_code == 500

#     def test_extension_abilities(self):
#         """Test GGUF extension abilities."""
#         expected_abilities = {
#             "discover_gguf_models",
#             "download_gguf_model",
#             "configure_gguf_settings",
#             "optimize_gguf_for_hardware",
#         }

#         assert expected_abilities.issubset(EXT_Local_AI_GGUF._abilities)

#     def test_hardware_optimization_exception_handling(
#         self, client, mock_manager, mock_user
#     ):
#         """Test hardware optimization endpoint exception handling."""
#         # Mock hardware detection to raise an exception
#         with patch(
#             "extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection"
#         ) as mock_hw:
#             mock_hw.side_effect = Exception("Hardware detection failed")

#             request_data = {"base_config_id": "test-config-123"}

#             response = client.post("/gguf/configs/hardware-optimize", json=request_data)

#             assert response.status_code == 500

#     def test_compatibility_check_exception_handling(
#         self, client, mock_manager, mock_user
#     ):
#         """Test compatibility check endpoint exception handling."""
#         # Mock hardware detection to raise an exception
#         with patch(
#             "extensions.local_ai.EXT_Local_AI.EXT_Local_AI.hardware_detection"
#         ) as mock_hw:
#             mock_hw.side_effect = Exception("Hardware detection failed")

#             config_id = "test-config-123"

#             response = client.get(f"/gguf/configs/{config_id}/compatibility")

#             assert response.status_code == 500

#     def test_load_model_exception_handling(self, client, mock_manager, mock_user):
#         """Test model loading endpoint exception handling."""
#         # Mock the BLL manager to raise an exception
#         with patch(
#             "extensions.local_ai_gguf.BLL_Local_AI_GGUF.GGUFModelConfigurationManager.load_model_instance"
#         ) as mock_load:
#             mock_load.side_effect = Exception("Load failed")

#             config_id = "test-config-123"

#             response = client.post(f"/gguf/configs/{config_id}/load-model")

#             assert response.status_code == 500

#     def test_discover_models_exception_handling(self, client, mock_manager, mock_user):
#         """Test model discovery endpoint exception handling."""
#         # Mock the extension static method to raise exception
#         with patch(
#             "extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.discover_gguf_models"
#         ) as mock_discover:
#             mock_discover.side_effect = Exception("Discovery failed")

#             request_data = {"search_paths": ["/custom/models"]}

#             response = client.post("/gguf/configs/discover-models", json=request_data)

#             assert response.status_code == 500

#     def test_download_model_exception_handling(self, client, mock_manager, mock_user):
#         """Test model download endpoint exception handling."""
#         # Mock the extension static method to raise exception
#         with patch(
#             "extensions.local_ai_gguf.EXT_Local_AI_GGUF.EXT_Local_AI_GGUF.download_gguf_model"
#         ) as mock_download:
#             mock_download.side_effect = Exception("Download failed")

#             request_data = {"model_id": "test/model", "quantization": "Q4_K_M"}

#             response = client.post("/gguf/configs/download-model", json=request_data)

#             assert response.status_code == 500

#     def test_static_routes_registration(self):
#         """Test that static routes are properly registered."""
#         # Test that extension has static route methods
#         assert hasattr(EXT_Local_AI_GGUF, "hardware_optimize_endpoint")
#         assert hasattr(EXT_Local_AI_GGUF, "compatibility_check_endpoint")
#         assert hasattr(EXT_Local_AI_GGUF, "load_model_instance_endpoint")
#         assert hasattr(EXT_Local_AI_GGUF, "generate_text_endpoint")
#         assert hasattr(EXT_Local_AI_GGUF, "generate_embeddings_endpoint")
#         assert hasattr(EXT_Local_AI_GGUF, "discover_models_endpoint")
#         assert hasattr(EXT_Local_AI_GGUF, "download_model_endpoint")
