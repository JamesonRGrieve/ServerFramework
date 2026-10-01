import base64
import io
import time
import uuid
from typing import Any, ClassVar, Dict, List, Optional, Set

import requests

try:
    from PIL import Image
except ImportError:
    Image = None

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    ability,
)
from zephyrex.extensions.ai.EXT_AI import EXT_AI
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_HF_INFERENCE_BASE = "https://api-inference.huggingface.co/models"
_DEFAULT_TEXT_MODEL = "HuggingFaceH4/zephyr-7b-beta"
_DEFAULT_IMAGE_MODEL = "runwayml/stable-diffusion-v1-5"
_REQUEST_TIMEOUT_SECONDS = 60


class HuggingFaceProvider(EXT_AI.AbstractProvider):
    """Static HuggingFace Inference API provider implementation."""

    name: ClassVar[str] = "HuggingFace"
    friendly_name: ClassVar[str] = "HuggingFace"
    platform: ClassVar[str] = "HuggingFace"

    _abilities: ClassVar[Set[str]] = {
        "text_generation",
        "image_generation",
    }

    _env: Dict[str, Any] = {
        "HUGGINGFACE_API_KEY": "",
    }

    @classmethod
    def get_platform_name(cls) -> str:
        return cls.platform

    @classmethod
    def services(cls) -> List[str]:
        return ["llm", "image"]

    @classmethod
    def validate_config(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> List[str]:
        # HuggingFace's inference API works unauthenticated for many public
        # models, so an API key is not strictly required.
        return []

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance]:
        """Bond a provider instance for API operations."""
        try:

            class BondedHuggingFaceInstance(AbstractProviderInstance):
                def __init__(self, provider_instance: ProviderInstanceModel):
                    super().__init__(provider_instance)
                    self.api_key = provider_instance.api_key or env(
                        "HUGGINGFACE_API_KEY"
                    )
                    self.model_name = (
                        provider_instance.model_name or _DEFAULT_TEXT_MODEL
                    )
                    self.text_api_url = f"{_HF_INFERENCE_BASE}/{self.model_name}"

                    settings = (
                        provider_instance.settings_json
                        if hasattr(provider_instance, "settings_json")
                        and provider_instance.settings_json
                        else {}
                    )
                    self.max_tokens = int(settings.get("max_tokens", 1024))
                    self.temperature = float(settings.get("temperature", 0.7))
                    self.max_retries = int(settings.get("max_retries", 3))
                    stable_diffusion_model = settings.get(
                        "stable_diffusion_model", _DEFAULT_IMAGE_MODEL
                    )
                    self.image_api_url = f"{_HF_INFERENCE_BASE}/{stable_diffusion_model}"

                @property
                def headers(self) -> Dict[str, str]:
                    if self.api_key:
                        return {"Authorization": f"Bearer {self.api_key}"}
                    return {}

            return BondedHuggingFaceInstance(instance)

        except Exception as e:
            logger.error(f"Error bonding HuggingFace instance: {e}")
            return None

    @classmethod
    @ability(name="text_generation")
    def generate_text(
        cls,
        bonded_instance: AbstractProviderInstance,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text using the HuggingFace Inference API."""
        payload = {
            "inputs": prompt,
            "parameters": {
                "temperature": (
                    temperature if temperature is not None else bonded_instance.temperature
                ),
                "max_new_tokens": max_tokens or bonded_instance.max_tokens,
                "return_full_text": False,
            },
        }

        max_retries = bonded_instance.max_retries
        tries = 0
        while True:
            tries += 1
            if tries > max_retries:
                return {
                    "success": False,
                    "error": f"Reached max retries: {max_retries}",
                }

            try:
                response = requests.post(
                    bonded_instance.text_api_url,
                    json=payload,
                    headers=bonded_instance.headers,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as e:
                return {"success": False, "error": str(e)}

            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(min(tries, 5))
                continue
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"API Error: {response.status_code}",
                }
            break

        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            return {
                "success": False,
                "error": f"Unexpected content type: {content_type}",
            }

        response_data = response.json()
        result = response_data[0]["generated_text"]
        return {
            "success": True,
            "text": result,
            "model": bonded_instance.model_name,
        }

    @classmethod
    @ability(name="embedding_generation")
    def generate_embeddings(
        cls, bonded_instance: AbstractProviderInstance, text: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate text embeddings using the HuggingFace feature-extraction API."""
        model = kwargs.get("model", "sentence-transformers/all-MiniLM-L6-v2")
        try:
            response = requests.post(
                f"{_HF_INFERENCE_BASE}/{model}",
                json={"inputs": text},
                headers=bonded_instance.headers,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            embedding = response.json()
            # Feature-extraction can return nested token embeddings; flatten
            # to a single vector when a 2D list is returned.
            if embedding and isinstance(embedding[0], list):
                embedding = embedding[0]
            return {
                "success": True,
                "embedding": embedding,
                "dimensions": len(embedding),
                "model": model,
            }
        except Exception as e:
            logger.error(f"Error generating embeddings with HuggingFace: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    @ability(name="image_generation")
    def generate_image(
        cls, bonded_instance: AbstractProviderInstance, prompt: str, **kwargs
    ) -> Dict[str, Any]:
        """Generate an image using a HuggingFace Stable Diffusion model."""
        if Image is None:
            return {"success": False, "error": "Pillow package not available"}

        try:
            response = requests.post(
                bonded_instance.image_api_url,
                headers=bonded_instance.headers,
                json={"inputs": prompt},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            image_data = response.content

            image = Image.open(io.BytesIO(image_data))
            filename = f"{uuid.uuid4()}.png"
            import os

            image_path = os.path.join("WORKSPACE", filename)
            os.makedirs("WORKSPACE", exist_ok=True)
            image.save(image_path)

            encoded_image_data = base64.b64encode(image_data).decode("utf-8")
            return {
                "success": True,
                "image": f"data:image/png;base64,{encoded_image_data}",
                "path": image_path,
            }
        except Exception as e:
            logger.error(f"Error generating image with HuggingFace: {e}")
            return {"success": False, "error": str(e)}
