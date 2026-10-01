# """
# Test suite for Local AI PyTorch Endpoint Layer.
# Tests PyTorch endpoint router and API functionality.
# """

# from unittest.mock import MagicMock, patch

# import pytest
# from fastapi import FastAPI
# from fastapi.testclient import TestClient

# from AbstractTest import CategoryOfTest, ClassOfTestsConfig
# from endpoints.AbstractEPTest import AbstractEndpointTest
# from extensions.local_ai_torch.EXT_Local_AI_Torch import EXT_Local_AI_Torch


# @pytest.mark.local_ai
# @pytest.mark.pytorch
# @pytest.mark.ep
# class TestEPLocalAITorch(AbstractEndpointTest):
#     """Test PyTorch extension @static_route functionality."""

#     test_config = ClassOfTestsConfig(categories=[CategoryOfTest.ENDPOINT])

#     @pytest.fixture
#     def app(self):
#         """Create FastAPI app with PyTorch extension for testing."""
#         app = FastAPI()
#         # Extension routes are automatically registered via @static_route
#         return app

#     @pytest.fixture
#     def client(self, app):
#         """Create test client."""
#         return TestClient(app)

#     @pytest.fixture
#     def mock_manager(self):
#         """Mock PyTorch configuration manager."""
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
#         """Test PyTorch extension configuration."""
#         assert EXT_Local_AI_Torch.name == "local_ai_pytorch"
#         assert EXT_Local_AI_Torch.friendly_name == "PyTorch Model Support"
#         assert hasattr(EXT_Local_AI_Torch, "_abilities")

#     def test_static_routes_registration(self):
#         """Test that static routes are properly registered."""
#         assert hasattr(EXT_Local_AI_Torch, "hardware_optimize_endpoint")
#         assert hasattr(EXT_Local_AI_Torch, "compatibility_check_endpoint")
#         assert hasattr(EXT_Local_AI_Torch, "load_pipeline_endpoint")
#         assert hasattr(EXT_Local_AI_Torch, "generate_text_endpoint")
#         assert hasattr(EXT_Local_AI_Torch, "generate_embeddings_endpoint")

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

#             response = client.post(
#                 "/pytorch/configs/hardware-optimize", json=request_data
#             )

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

#             response = client.get(f"/pytorch/configs/{config_id}/compatibility")

#             assert response.status_code == 200
#             data = response.json()
#             assert data["success"] is True
#             assert "compatible" in data
#             assert "issues" in data
#             assert "recommendations" in data
#             assert "estimated_vram_gb" in data

#     def test_load_pipeline_endpoint(self, client, mock_manager, mock_user):
#         """Test model pipeline loading endpoint."""
#         config_id = "test-config-123"
#         request_data = {"task": "text-generation"}

#         response = client.post(
#             f"/pytorch/configs/{config_id}/load-pipeline", json=request_data
#         )

#         assert response.status_code == 200
#         data = response.json()
#         assert data["success"] is True
#         assert data["config_id"] == config_id
#         assert data["task"] == "text-generation"

#     def test_generate_text_endpoint(self, client, mock_manager, mock_user):
#         """Test text generation endpoint."""
#         config_id = "test-config-123"
#         request_data = {
#             "prompt": "Hello, world!",
#             "max_new_tokens": 100,
#             "temperature": 0.7,
#         }

#         response = client.post(
#             f"/pytorch/configs/{config_id}/generate-text", json=request_data
#         )

#         assert response.status_code == 200
#         data = response.json()
#         assert data["success"] is True
#         assert "generated_text" in data
#         assert "usage" in data
#         assert "model_info" in data

#     def test_generate_embeddings_endpoint(self, client, mock_manager, mock_user):
#         """Test embedding generation endpoint."""
#         config_id = "test-config-123"
#         request_data = {"text": "Text to embed", "normalize": True}

#         response = client.post(
#             f"/pytorch/configs/{config_id}/generate-embeddings", json=request_data
#         )

#         assert response.status_code == 200
#         data = response.json()
#         assert data["success"] is True
#         assert len(data["embeddings"]) == 768
#         assert data["dimension"] == 768
#         assert "model_info" in data

#     def test_text_generation_failure(self, client, mock_manager, mock_user):
#         """Test text generation endpoint failure handling."""
#         # Mock the extension to return an error in the response
#         config_id = "test-config-123"
#         request_data = {"prompt": "Hello, world!", "max_new_tokens": 100}

#         # Test with invalid config_id or other error condition
#         with patch(
#             "extensions.local_ai_torch.EXT_Local_AI_Torch.EXT_Local_AI_Torch.generate_text_endpoint"
#         ) as mock_endpoint:
#             mock_endpoint.side_effect = Exception("Model not available")

#             response = client.post(
#                 f"/pytorch/configs/{config_id}/generate-text", json=request_data
#             )

#             # Depending on error handling, this might be 500 or 400
#             assert response.status_code in [400, 500]

#     def test_embedding_generation_failure(self, client, mock_manager, mock_user):
#         """Test embedding generation endpoint failure handling."""
#         config_id = "test-config-123"
#         request_data = {"text": "Text to embed", "normalize": True}

#         # Test with exception in endpoint
#         with patch(
#             "extensions.local_ai_torch.EXT_Local_AI_Torch.EXT_Local_AI_Torch.generate_embeddings_endpoint"
#         ) as mock_endpoint:
#             mock_endpoint.side_effect = Exception("Embedding model not available")

#             response = client.post(
#                 f"/pytorch/configs/{config_id}/generate-embeddings", json=request_data
#             )

#             assert response.status_code in [400, 500]

#     def test_extension_abilities(self):
#         """Test PyTorch extension abilities."""
#         expected_abilities = {
#             "discover_pytorch_models",
#             "download_pytorch_model",
#             "mount_pytorch_model",
#             "unmount_pytorch_model",
#             "configure_pytorch_settings",
#             "optimize_pytorch_for_hardware",
#         }

#         assert expected_abilities.issubset(EXT_Local_AI_Torch._abilities)

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

#             response = client.post(
#                 "/pytorch/configs/hardware-optimize", json=request_data
#             )

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

#             response = client.get(f"/pytorch/configs/{config_id}/compatibility")

#             assert response.status_code == 500

#     def test_load_pipeline_exception_handling(self, client, mock_manager, mock_user):
#         """Test pipeline loading endpoint exception handling."""
#         # Mock the endpoint method to raise an exception
#         with patch(
#             "extensions.local_ai_torch.EXT_Local_AI_Torch.EXT_Local_AI_Torch.load_pipeline_endpoint"
#         ) as mock_endpoint:
#             mock_endpoint.side_effect = Exception("Pipeline load failed")

#             config_id = "test-config-123"
#             request_data = {"task": "text-generation"}

#             response = client.post(
#                 f"/pytorch/configs/{config_id}/load-pipeline", json=request_data
#             )

#             assert response.status_code == 500

#     def test_dependencies_configuration(self):
#         """Test PyTorch extension dependencies."""
#         dependencies = EXT_Local_AI_Torch.dependencies
#         assert dependencies is not None

#         # Check for required local_ai dependency
#         ext_deps = [
#             dep
#             for dep in dependencies.dependencies
#             if hasattr(dep, "name") and dep.name == "local_ai"
#         ]
#         assert len(ext_deps) > 0
#         assert ext_deps[0].is_required is True
