"""
AI extension business logic layer.

Provides AI request tracking (prompt/response/cost/token bookkeeping) and
per-provider usage statistics, plus the AI-request generation helpers used
by the Provider Rotation System.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)


class AiRequestModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    metaclass=ModelMeta,
):
    """Model for tracking AI requests and their metadata."""

    prompt: str = Field(..., description="The prompt sent to the AI provider")
    response: Optional[str] = Field(
        None, description="The response from the AI provider"
    )
    model_name: Optional[str] = Field(None, description="The AI model used")
    provider_instance_id: str = Field(
        ..., description="Provider instance that handled the request"
    )
    tokens_used: Optional[int] = Field(None, description="Number of tokens consumed")
    cost: Optional[float] = Field(None, description="Cost of the request")
    status: str = Field(
        default="pending", description="Request status: pending, completed, failed"
    )
    error_message: Optional[str] = Field(
        None, description="Error message if request failed"
    )
    request_type: str = Field(
        ...,
        description="Type of AI request: text_generation, embedding, image_generation, etc.",
    )

    # Database metadata
    table_comment: ClassVar[str] = "Tracks AI requests and their associated metadata"
    is_system_entity: ClassVar[bool] = False

    class Create(BaseModel, NameMixinModel):
        prompt: str = Field(..., description="The prompt sent to the AI provider")
        provider_instance_id: str = Field(
            ..., description="Provider instance that handled the request"
        )
        model_name: Optional[str] = Field(None, description="The AI model used")
        request_type: str = Field(..., description="Type of AI request")

    class Update(BaseModel, NameMixinModel.Optional):
        response: Optional[str] = Field(
            None, description="The response from the AI provider"
        )
        tokens_used: Optional[int] = Field(
            None, description="Number of tokens consumed"
        )
        cost: Optional[float] = Field(None, description="Cost of the request")
        status: Optional[str] = Field(None, description="Request status")
        error_message: Optional[str] = Field(
            None, description="Error message if request failed"
        )

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, NameMixinModel.Search
    ):
        prompt: Optional[StringSearchModel] = None
        response: Optional[StringSearchModel] = None
        model_name: Optional[StringSearchModel] = None
        provider_instance_id: Optional[StringSearchModel] = None
        tokens_used: Optional[NumericalSearchModel] = None
        cost: Optional[NumericalSearchModel] = None
        status: Optional[StringSearchModel] = None
        error_message: Optional[StringSearchModel] = None
        request_type: Optional[StringSearchModel] = None


class AiRequestManager(AbstractBLLManager, RouterMixin):
    """Manager for AI requests with custom routes for AI functionality."""

    _model = AiRequestModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/ai"
    tags: ClassVar[Optional[List[str]]] = ["AI Services"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    auth_dependency: ClassVar[Optional[str]] = "get_current_user"

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ) -> None:
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    # Custom AI routes would be added here as methods
    # Following the pattern where endpoints are declared as custom routes on managers

    async def generate_text(
        self,
        prompt: str,
        provider_instance_id: Optional[str] = None,
        model_name: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Generate text using AI provider rotation system."""
        try:
            # This would use the provider rotation system to generate text
            # Implementation would call the appropriate provider via rotation
            request_data = {
                "name": f"text_gen_{datetime.now().isoformat()}",
                "prompt": prompt,
                "provider_instance_id": provider_instance_id or "default",
                "model_name": model_name,
                "request_type": "text_generation",
            }

            # Create AI request record
            ai_request = self.create(**request_data)

            # TODO: Implement actual provider rotation call here
            # This would integrate with EXT_AI.root.rotate() system

            return {
                "request_id": ai_request.id,
                "status": "pending",
                "message": "AI request created and queued",
            }

        except Exception as e:
            logger.error(f"Error in generate_text: {e}")
            raise HTTPException(
                status_code=500, detail=f"Failed to generate text: {str(e)}"
            )

    async def create_embedding(
        self,
        text: str,
        provider_instance_id: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create text embedding using AI provider rotation system."""
        try:
            request_data = {
                "name": f"embedding_{datetime.now().isoformat()}",
                "prompt": text,
                "provider_instance_id": provider_instance_id or "default",
                "model_name": model_name,
                "request_type": "embedding",
            }

            ai_request = self.create(**request_data)

            # TODO: Implement actual provider rotation call here

            return {
                "request_id": ai_request.id,
                "status": "pending",
                "message": "Embedding request created and queued",
            }

        except Exception as e:
            logger.error(f"Error in create_embedding: {e}")
            raise HTTPException(
                status_code=500, detail=f"Failed to create embedding: {str(e)}"
            )

    async def generate_image(
        self,
        prompt: str,
        provider_instance_id: Optional[str] = None,
        model_name: Optional[str] = None,
        size: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate image using AI provider rotation system."""
        try:
            request_data = {
                "name": f"image_gen_{datetime.now().isoformat()}",
                "prompt": prompt,
                "provider_instance_id": provider_instance_id or "default",
                "model_name": model_name,
                "request_type": "image_generation",
            }

            ai_request = self.create(**request_data)

            # TODO: Implement actual provider rotation call here

            return {
                "request_id": ai_request.id,
                "status": "pending",
                "message": "Image generation request created and queued",
            }

        except Exception as e:
            logger.error(f"Error in generate_image: {e}")
            raise HTTPException(
                status_code=500, detail=f"Failed to generate image: {str(e)}"
            )


# AI Statistics Model for tracking usage across the system
class AiStatisticsModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    """Model for tracking AI usage statistics."""

    date: datetime = Field(..., description="Date for these statistics")
    provider_name: str = Field(..., description="Name of the AI provider")
    model_name: Optional[str] = Field(None, description="Specific model name")
    request_count: int = Field(default=0, description="Number of requests")
    tokens_used: int = Field(default=0, description="Total tokens consumed")
    total_cost: float = Field(default=0.0, description="Total cost")
    success_rate: float = Field(default=1.0, description="Success rate percentage")

    # Database metadata
    table_comment: ClassVar[str] = "Daily statistics for AI usage by provider and model"
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

    class Create(BaseModel):
        date: datetime = Field(..., description="Date for these statistics")
        provider_name: str = Field(..., description="Name of the AI provider")
        model_name: Optional[str] = Field(None, description="Specific model name")
        request_count: int = Field(default=0, description="Number of requests")
        tokens_used: int = Field(default=0, description="Total tokens consumed")
        total_cost: float = Field(default=0.0, description="Total cost")

    class Update(BaseModel):
        request_count: Optional[int] = Field(None, description="Number of requests")
        tokens_used: Optional[int] = Field(None, description="Total tokens consumed")
        total_cost: Optional[float] = Field(None, description="Total cost")
        success_rate: Optional[float] = Field(
            None, description="Success rate percentage"
        )

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        date: Optional[DateSearchModel] = None
        provider_name: Optional[StringSearchModel] = None
        model_name: Optional[StringSearchModel] = None
        request_count: Optional[NumericalSearchModel] = None
        tokens_used: Optional[NumericalSearchModel] = None
        total_cost: Optional[NumericalSearchModel] = None
        success_rate: Optional[NumericalSearchModel] = None


class AiStatisticsManager(AbstractBLLManager):
    """Manager for AI statistics tracking."""

    _model = AiStatisticsModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ) -> None:
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )


# Seed data functions for Provider Registration
def seed_data() -> List[Dict[str, Any]]:
    """
    Return seed data for AI providers and instances.
    Delegates to the static extension method.
    """
    return EXT_AI.get_seed_data()


def register_ai_providers_hook():
    """Hook to register AI providers in the core Provider table"""
    seed_dict = EXT_AI.get_seed_data()
    providers_to_add = seed_dict.get("providers", [])

    for provider in providers_to_add:
        logger.debug(f"Registering {provider['name']} provider via AI extension hook")

    return providers_to_add


def register_ai_provider_instances_hook():
    """Hook to register AI provider instances in the core ProviderInstance table"""
    seed_dict = EXT_AI.get_seed_data()
    instances_to_add = seed_dict.get("instances", [])

    for instance in instances_to_add:
        logger.debug(
            f"Registering {instance['name']} provider instance via AI extension hook"
        )

    return instances_to_add


def transcribe_audio_to_text(manager, **kwargs) -> str:
    """
    Transcribe audio file to text using AI provider.

    Args:
        audio_file: Path to the audio file to transcribe

    Returns:
        Transcribed text from the audio file
    """
    audio_file = kwargs["file"]

    model_registry = (
        manager.model_registry if hasattr(manager, "model_registry") else None
    )

    from zephyrex.logic.BLL_Providers import RotationManager

    ai_rotation_manager = RotationManager(
        requester_id=manager.requester.id,
        model_registry=model_registry,
    )

    # "Root_Ai" is the framework's own root-rotation naming convention for
    # this extension (``RotationModel.seed_data`` PascalCases each
    # underscore-delimited part of the extension name — "ai" -> "Ai" —
    # exactly matching this literal). Seeding only emits that row once
    # provider discovery has found at least one provider for the extension,
    # so a fresh/degraded deployment (or a consumer test harness whose
    # provider discovery hasn't run yet) can legitimately have no such row
    # yet. Rather than hard-crash on a 404 in that case, create it lazily so
    # this function is self-sufficient regardless of seeding order.
    try:
        rotation = ai_rotation_manager.get(name="Root_Ai")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        rotation = ai_rotation_manager.create(
            name="Root_Ai",
            description="Root rotation for the ai extension.",
        )
    ai_rotation_manager.target_id = rotation.id

    from zephyrex.extensions.ai.EXT_AI import EXT_AI

    EXT_AI.root = ai_rotation_manager

    ext_instance = EXT_AI()
    response = ext_instance.transcribe_audio(audio_file=audio_file)
    if response and response.get("success") and response.get("text"):
        return response["text"]
    else:
        logger.error(
            f"Failed to transcribe audio file {audio_file} using Root Ai rotation {rotation.id}"
        )
        return "Transcription failed"
