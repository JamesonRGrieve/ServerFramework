import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from pydantic import BaseModel

from zephyrex.extensions.ai_agents.BLL_AI_Agents import AgentManager
from zephyrex.extensions.ai_memories.BLL_AI_Memories import MemoryManager
from zephyrex.logic.BLL_Auth import UserManager, UserModel


# ``zephyrex.endpoints.AbstractEndpointRouter.MessageModel`` does not exist in the
# current static framework (no shared endpoint-router abstraction has been built
# yet — see the framework's own auth_oauth extension, which hits the same gap).
# Define the trivial response envelope locally instead of depending on it.
class MessageModel(BaseModel):
    message: str


# Define Pydantic models for request body
class FinetuneAgentModel(BaseModel):
    model: Optional[str] = "unsloth/mistral-7b-v0.2"
    max_seq_length: Optional[int] = 16384
    huggingface_output_path: Optional[str] = "JamesonRGrieve/finetuned-mistral-7b-v0.2"
    private_repo: Optional[bool] = True
    dataset_name: Optional[str] = "dataset"


# Shared implementation function for both routers
async def _fine_tune_model_impl(
    agent_id: str, finetune: FinetuneAgentModel, user: UserModel
) -> MessageModel:
    """
    Shared implementation for fine-tuning a language model for an agent.

    Args:
        agent_id: ID of the agent to fine-tune
        finetune: Fine-tuning configuration
        user: Authenticated user

    Returns:
        Message confirming fine-tuning has started
    """
    # Check if user has admin role
    if not user.is_admin:
        raise HTTPException(
            status_code=403, detail="This operation requires admin privileges"
        )

    # Get agent to validate existence and update settings
    agent_manager = AgentManager(requester_id=user.id)
    agent = agent_manager.get(id=agent_id)

    # Update agent settings to indicate training is in progress
    agent_settings = agent_manager.get_agent_settings(agent_id=agent_id)
    agent_settings["training"] = True
    agent_manager.update_agent_settings(agent_id=agent_id, settings=agent_settings)

    # Start the fine-tuning process asynchronously
    asyncio.create_task(
        _run_fine_tuning(
            user_id=user.id,
            agent_id=agent_id,
            agent_name=agent.name,
            dataset_name=finetune.dataset_name,
            model_name=finetune.model,
            max_seq_length=finetune.max_seq_length,
            huggingface_output_path=finetune.huggingface_output_path,
            private_repo=finetune.private_repo,
        )
    )

    return MessageModel(
        message=f"Fine-tuning of model {finetune.model} started. The agent's training status has been set to 'True' and will be set to 'False' once the training is complete."
    )


async def _get_tuning_status_impl(
    agent_id: str,
    user: UserModel,
) -> Dict[str, Any]:
    """
    Shared implementation for getting the current status of fine-tuning for an agent.

    Args:
        agent_id: ID of the agent to check
        user: Authenticated user

    Returns:
        Current fine-tuning status information
    """
    # Get agent settings
    agent_manager = AgentManager(requester_id=user.id)
    agent = agent_manager.get(id=agent_id)
    agent_settings = agent_manager.get_agent_settings(agent_id=agent_id)

    # Extract relevant information
    is_training = agent_settings.get("training", False)
    model_info = {
        "base_model": agent_settings.get("model", "default"),
        "fine_tuned_model": agent_settings.get("fine_tuned_model", None),
    }

    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "training": is_training,
        "model_info": model_info,
    }


async def _run_fine_tuning(
    user_id: str,
    agent_id: str,
    agent_name: str,
    dataset_name: str,
    model_name: str,
    max_seq_length: int,
    huggingface_output_path: str,
    private_repo: bool,
) -> None:
    """
    Run the fine-tuning process in a separate task.

    This function handles:
    1. Importing necessary libraries
    2. Creating a dataset from agent memories if needed
    3. Fine-tuning the model
    4. Updating agent settings when complete

    Args:
        user_id: ID of the user initiating the process
        agent_id: ID of the agent being fine-tuned
        agent_name: Name of the agent
        dataset_name: Name of the dataset to use
        model_name: Base model to fine-tune
        max_seq_length: Maximum sequence length for the model
        huggingface_output_path: Path to save model on HuggingFace
        private_repo: Whether to make the HuggingFace repo private
    """
    try:
        # Import required libraries (importing here to avoid loading them for every request)
        import copy
        import os
        import subprocess
        import sys

        # Install required packages if not available
        required_packages = [
            "torch",
            "transformers",
            "peft",
            "bitsandbytes",
            "trl",
            "unsloth",
        ]

        for package in required_packages:
            try:
                __import__(package)
            except ImportError:
                if package == "unsloth":
                    subprocess.check_call(
                        [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git",
                        ]
                    )
                else:
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", package]
                    )

        # Import modules now that they're installed
        import bitsandbytes as bnb
        import torch
        from bitsandbytes.functional import dequantize_4bit
        from peft import PeftModel
        from peft.utils import _get_submodules
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            TrainingArguments,
        )
        from trl import DPOTrainer
        from unsloth import FastLanguageModel

        agent_manager = AgentManager(requester_id=user_id)
        memory_manager = MemoryManager(requester_id=user_id)

        # Get agent settings
        agent_settings = agent_manager.get_agent_settings(agent_id=agent_id)
        huggingface_api_key = agent_settings.get("HUGGINGFACE_API_KEY")

        # Step 1: Create dataset if needed
        response = await memory_manager.create_dataset_from_memories(
            agent_id=agent_id, dataset_name=dataset_name, batch_size=5
        )

        # Extract dataset path
        output_path = "./models"
        dataset_path = f"./WORKSPACE/{agent_name}/datasets/{dataset_name}.json"

        # Step 2: Create qLora adapter
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_name,
            max_seq_length=max_seq_length,
            load_in_4bit=True,
            token=huggingface_api_key,
        )

        model = FastLanguageModel.get_peft_model(
            model,
            r=16,
            lora_alpha=16,
            lora_dropout=0,
            bias="none",
            use_gradient_checkpointing=True,
        )

        training_args = TrainingArguments(output_dir="./WORKSPACE")
        train_dataset = torch.load(dataset_path)

        dpo_trainer = DPOTrainer(
            model,
            model_ref=None,
            args=training_args,
            beta=0.1,
            train_dataset=train_dataset,
            tokenizer=tokenizer,
        )

        dpo_trainer.train()
        adapter_path = dpo_trainer.model_path

        # Step 3: Merge base model with qLora adapter
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )

        model, tokenizer = AutoModelForCausalLM.from_pretrained(
            model_name,
            load_in_4bit=True,
            torch_dtype=torch.bfloat16,
            quantization_config=quantization_config,
            device_map="auto",
            token=huggingface_api_key,
        ), AutoTokenizer.from_pretrained(model_name)

        os.makedirs(output_path, exist_ok=True)

        # Convert model parameters
        for name, module in model.named_modules():
            if isinstance(module, bnb.nn.Linear4bit):
                quant_state = copy.deepcopy(module.weight.quant_state)
                quant_state.dtype = torch.bfloat16
                weights = dequantize_4bit(
                    module.weight.data, quant_state=quant_state, quant_type="nf4"
                ).to(torch.bfloat16)
                new_module = torch.nn.Linear(
                    module.in_features,
                    module.out_features,
                    bias=None,
                    dtype=torch.bfloat16,
                )
                new_module.weight = torch.nn.Parameter(weights)
                new_module.to(device="cuda", dtype=torch.bfloat16)
                parent, target, target_name = _get_submodules(model, name)
                setattr(parent, target_name, new_module)

        model.is_loaded_in_4bit = False
        model.save_pretrained(output_path)
        tokenizer.save_pretrained(output_path)

        # Apply LoRA adapter
        model = PeftModel.from_pretrained(model=model, model_id=adapter_path)
        model = model.merge_and_unload()
        model.save_pretrained(
            output_path, safe_serialization=True, max_shard_size="4GB"
        )

        # Push to HuggingFace if API key is provided
        if huggingface_api_key:
            model.push_to_hub(
                huggingface_output_path, use_temp_dir=False, private=private_repo
            )
            tokenizer.push_to_hub(
                huggingface_output_path, use_temp_dir=False, private=private_repo
            )

        # Update agent settings
        agent_settings["training"] = False
        agent_manager.update_agent_settings(agent_id=agent_id, settings=agent_settings)

    except Exception as e:
        # Handle errors: log and update agent settings
        import logging

        logging.error(f"Error in fine-tuning: {str(e)}")

        # Update agent settings to indicate training is no longer in progress
        agent_manager = AgentManager(requester_id=user_id)
        agent_settings = agent_manager.get_agent_settings(agent_id=agent_id)
        agent_settings["training"] = False
        agent_manager.update_agent_settings(agent_id=agent_id, settings=agent_settings)


# Create the router for the standardized path (under agent)
agent_tune_router = APIRouter(
    prefix="/v1/agent/{agent_id}/tune",
    tags=["Agent Tuning"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Not authenticated"},
        status.HTTP_403_FORBIDDEN: {"description": "Permission denied"},
        status.HTTP_404_NOT_FOUND: {"description": "Resource not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Server error"},
    },
)

# Create the original router for backward compatibility
tuning_router = APIRouter(
    prefix="/v1/tuning",
    tags=["Model Tuning"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Not authenticated"},
        status.HTTP_403_FORBIDDEN: {"description": "Permission denied"},
        status.HTTP_404_NOT_FOUND: {"description": "Resource not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Server error"},
    },
)


# Add endpoints to the agent tuning router
@agent_tune_router.post(
    "/fine-tune",
    summary="Fine-tune agent model",
    description="""
    Initiates the fine-tuning process for an agent's language model using memories as training data.

    This endpoint starts an asynchronous process to:
    1. Create a synthetic dataset from the agent's memories
    2. Fine-tune a language model using DPO (Direct Preference Optimization)
    3. Optionally push the resulting model to HuggingFace

    The process runs in the background and may take a significant amount of time
    depending on the size of the dataset and the selected model.

    ## Optional Parameters
    - `model`: Base model to fine-tune (default: "unsloth/mistral-7b-v0.2")
    - `max_seq_length`: Maximum sequence length for the model (default: 16384)
    - `huggingface_output_path`: Path to save model on HuggingFace (default: "JamesonRGrieve/finetuned-mistral-7b-v0.2")
    - `private_repo`: Whether to make the HuggingFace repo private (default: true)
    - `dataset_name`: Name of the dataset to use or create (default: "dataset")

    ## Response
    A message confirming that the fine-tuning process has started.
    """,
    response_model=MessageModel,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Fine-tuning process started",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Fine-tuning of model unsloth/mistral-7b-v0.2 started. The agent's training status has been set to 'True' and will be set to 'False' once the training is complete."
                    }
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {
            "description": "Invalid parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid model name or configuration"}
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Agent not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Agent with specified ID not found"}
                }
            },
        },
    },
)
async def fine_tune_agent_model(
    agent_id: str = Path(..., description="Agent ID"),
    finetune: FinetuneAgentModel = Body(...),
    user: UserModel = Depends(UserManager.auth),
) -> MessageModel:
    """Fine-tune an agent's language model."""
    return await _fine_tune_model_impl(agent_id, finetune, user)


@agent_tune_router.get(
    "/status",
    summary="Get fine-tuning status",
    description="Retrieves the current status of fine-tuning for an agent.",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Fine-tuning status retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "agent_id": "a1g2e3n4-5678-90ab-cdef-123456789012",
                        "training": False,
                        "model_info": {
                            "base_model": "unsloth/mistral-7b-v0.2",
                            "fine_tuned_model": "JamesonRGrieve/finetuned-mistral-7b-v0.2",
                        },
                    }
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Agent not found",
        },
    },
)
async def get_agent_tuning_status(
    agent_id: str = Path(..., description="Agent ID"),
    user: UserModel = Depends(UserManager.auth),
) -> Dict[str, Any]:
    """Get the current status of fine-tuning for an agent."""
    return await _get_tuning_status_impl(agent_id, user)


# Combine the routers
router = APIRouter()
router.include_router(agent_tune_router)
router.include_router(tuning_router)
