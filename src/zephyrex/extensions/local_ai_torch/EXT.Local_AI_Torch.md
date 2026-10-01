# Local AI PyTorch Extension

PyTorch model extension for AGInfrastructure providing transformer model support through the Provider Rotation System via HuggingFace integration.

## Overview

The `local_ai_pytorch` extension extends the Local AI framework to provide PyTorch model support through:
- **Automatic Model Discovery**: Scans directories for PyTorch models and creates Provider records
- **Dynamic Provider Creation**: Creates providers for each discovered PyTorch model
- **HuggingFace Integration**: Seamless integration with HuggingFace transformers
- **Hardware Optimization**: Automatic optimization for NVIDIA, AMD, Intel, and Apple Silicon
- **Quantization Support**: 8-bit and 4-bit quantization for memory efficiency
- **Multi-Modal Support**: Text, vision, audio, and multimodal models
- **Provider Rotation Integration**: Full integration with the Provider Rotation System

## Architecture

### Extension Structure
```python
class EXT_Local_AI_Torch(AbstractStaticExtension):
    """PyTorch model extension providing meta abilities for model management."""
    
    name: ClassVar[str] = "local_ai_pytorch"
    dependencies: ClassVar[Dependencies] = Dependencies([
        EXT_Dependency("local_ai", is_required=True),
        PIP_Dependency("torch", semver=">=1.12.0"),
        PIP_Dependency("transformers", semver=">=4.20.0")
    ])
    
    # Meta abilities for model management
    _abilities: ClassVar[set] = {
        "discover_pytorch_models",
        "download_pytorch_model",
        "mount_pytorch_model",
        "unmount_pytorch_model",
        "configure_pytorch_settings",
        "optimize_pytorch_for_hardware",
    }
```

### Provider Implementation
```python
class PRV_PyTorch_Model(AbstractAIProvider):
    """Concrete PyTorch model provider for AI inference."""
    
    extension_type: ClassVar[str] = "local_ai_pytorch"
    
    # AI transformation abilities
    _abilities: ClassVar[set] = {
        "text_to_text",
        "text_to_embedding",
        # Additional abilities based on model type
    }
    
    @classmethod
    def bond_instance(cls, instance_data: Dict[str, Any]) -> bool:
        """Load PyTorch model into memory."""
        # Loads model using transformers library
        
    @ability
    @classmethod
    def text_to_text(cls, prompt: str, **kwargs) -> Dict[str, Any]:
        """Generate text using PyTorch model."""
```

## Model Discovery and Registration

### Automatic Model Discovery
```python
# Extension discovers PyTorch models and creates Provider metadata
result = EXT_Local_AI_Torch.discover_pytorch_models(
    search_paths=[
        "./models/pytorch",
        "~/.cache/huggingface/hub"
    ]
)

# Returns discovered models with metadata
{
    "success": True,
    "discovered_models": [
        {
            "name": "meta-llama/Llama-2-7b-chat-hf",
            "model_path": "/models/pytorch/Llama-2-7b-chat-hf",
            "estimated_vram_gb": 13.5,
            "model_type": "pytorch_hf",
            "capabilities": ["TextGeneration", "ChatCompletion"],
            "context_window": 4096,
            "parameter_count": "7b",
            "model_class": "LlamaForCausalLM",
            "torch_dtype": "float16"
        }
    ],
    "count": 1
}
```

### Provider Registration Flow
1. Extension discovers PyTorch models in configured directories
2. Analyzes model config.json to determine capabilities
3. Creates Provider records with appropriate metadata
4. Provider instances represent specific configurations (device map, quantization)
5. Providers available through rotation system

## Configuration

### Environment Variables
```python
_env: ClassVar[Dict[str, Any]] = {
    "PYTORCH_MODELS_DIR": {
        "type": str,
        "default": "./models/pytorch",
        "description": "Directory to store PyTorch models",
    },
    "PYTORCH_AUTO_DISCOVER": {
        "type": bool,
        "default": True,
        "description": "Automatically discover PyTorch models on startup",
    },
    "PYTORCH_DEFAULT_DTYPE": {
        "type": str,
        "default": "float16",
        "description": "Default torch dtype (float16, bfloat16, float32)",
    },
    "PYTORCH_DEVICE_MAP": {
        "type": str,
        "default": "auto",
        "description": "Default device map strategy",
    },
}
```

### Advanced Settings
Provider instances can be configured with advanced settings:

```python
# Configure advanced PyTorch settings
result = EXT_Local_AI_Torch.configure_pytorch_settings(
    provider_instance,
    settings={
        "USE_BEAM_SEARCH": "true",
        "DEVICE_MAP": "balanced",
        "TORCH_DTYPE": "bfloat16",
        "LOAD_IN_8BIT": "true",
        "USE_FLASH_ATTENTION": "true",
    }
)
```

## Hardware Optimization

### Automatic Hardware Detection and Optimization
```python
# Get hardware info from local_ai extension
hardware_info = EXT_Local_AI.hardware_detection()

# Optimize PyTorch configuration for hardware
optimization_result = EXT_Local_AI_Torch.optimize_pytorch_for_hardware(
    hardware_info,
    model_requirements={"estimated_vram_gb": 13.5}
)

# Returns optimized configuration
{
    "success": True,
    "optimization_config": {
        "hardware_type": "nvidia",
        "device": "cuda",
        "torch_dtype": "float16",
        "device_map": "auto",
        "low_cpu_mem_usage": True,
        "load_in_8bit": False
    }
}
```

### Hardware-Specific Optimizations
- **NVIDIA**: CUDA acceleration with mixed precision and Flash Attention
- **Apple Silicon**: MPS backend with optimized memory usage
- **AMD**: ROCm support through CUDA interface
- **Intel**: CPU optimizations with AVX512 support
- **Multi-GPU**: Automatic model sharding across devices

## Usage via Provider Rotation

### Text Generation
```python
from rotation.RotationManager import RotationManager

# Generate text using rotation system
result = RotationManager.rotate(
    "text_to_text",
    prompt="Explain quantum computing in simple terms.",
    max_tokens=200,
    temperature=0.8,
    provider_filter={"extension_type": "local_ai_pytorch"}
)

print(result["text"])
```

### Chat Completion
```python
# Chat format with conversation history
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is machine learning?"}
]

result = RotationManager.rotate(
    "text_to_text",
    prompt=messages,
    max_tokens=150,
    provider_filter={
        "extension_type": "local_ai_pytorch",
        "capabilities": ["ChatCompletion"]
    }
)
```

### Embedding Generation
```python
# Generate embeddings for text
result = RotationManager.rotate(
    "text_to_embedding",
    text=["Hello world", "How are you?"],
    normalize=True,
    provider_filter={
        "extension_type": "local_ai_pytorch",
        "capabilities": ["EmbeddingGeneration"]
    }
)

embeddings = result["embedding"]
```

## Model Management

### Download Models
```python
# Download a PyTorch model from HuggingFace
result = EXT_Local_AI_Torch.download_pytorch_model(
    model_id="meta-llama/Llama-2-7b-chat-hf",
    revision="main"
)

# Returns download status
{
    "success": True,
    "download_id": "pytorch_meta-llama/Llama-2-7b-chat-hf_main_5678",
    "status": "accepted",
    "estimated_size_gb": 13.5
}
```

### Mount/Unmount Models
```python
# Mount model to GPU/memory
mount_result = EXT_Local_AI_Torch.mount_pytorch_model(
    provider_instance,
    estimated_vram_gb=13.5
)

# Unmount when done
unmount_result = EXT_Local_AI_Torch.unmount_pytorch_model(
    provider_instance
)
```

## Quantization Support

### 8-bit and 4-bit Quantization
```python
# Configure quantization settings
settings = {
    "LOAD_IN_8BIT": "true",  # 8-bit quantization
    # or
    "LOAD_IN_4BIT": "true",  # 4-bit quantization
    "BNBCONFIG": {
        "load_in_4bit": True,
        "bnb_4bit_compute_dtype": "float16",
        "bnb_4bit_quant_type": "nf4",
        "bnb_4bit_use_double_quant": True
    }
}
```

### Quantization Benefits
| Type | Memory Reduction | Performance Impact | Use Case |
|------|------------------|-------------------|----------|
| FP32 | 0% | Baseline | Maximum accuracy |
| FP16 | 50% | Minimal | Standard GPU inference |
| BF16 | 50% | Minimal | Better for training |
| INT8 | 75% | Small | Memory-constrained |
| INT4 | 87.5% | Moderate | Extreme memory savings |

## Multi-Modal Support

### Vision-Language Models
```python
# Image + text input
result = RotationManager.rotate(
    "image_to_text",
    image_path="/path/to/image.jpg",
    prompt="What is in this image?",
    provider_filter={
        "extension_type": "local_ai_pytorch",
        "capabilities": ["VisionLanguageChat"]
    }
)
```

### Audio Models
```python
# Speech-to-text
result = RotationManager.rotate(
    "audio_to_text",
    audio_path="/path/to/audio.wav",
    provider_filter={
        "extension_type": "local_ai_pytorch",
        "capabilities": ["SpeechToText"]
    }
)
```

## Advanced Features

### Flash Attention
Enable Flash Attention 2 for faster inference:
```python
settings = {
    "USE_FLASH_ATTENTION": "true"
}
```

### Gradient Checkpointing
For training or fine-tuning:
```python
settings = {
    "GRADIENT_CHECKPOINTING": "true"
}
```

### Custom Device Maps
```python
settings = {
    "DEVICE_MAP": {
        "model.embed_tokens": 0,
        "model.layers.0-15": 0,
        "model.layers.16-31": 1,
        "lm_head": 1
    }
}
```

## Integration with Payment Extension

Supports subscription-based prioritization when payment extension is available:
- Premium users get priority GPU allocation
- Enterprise users can reserve dedicated instances
- Standard users share resources with fair queuing

## Testing

```python
class TestPyTorchExtension(AbstractEXTTest):
    extension_class = EXT_Local_AI_Torch
    
    def test_model_discovery(self):
        """Test PyTorch model discovery."""
        result = self.extension_class.discover_pytorch_models()
        assert result["success"]
        assert "discovered_models" in result
    
    def test_hardware_optimization(self):
        """Test hardware optimization."""
        hardware_info = {
            "primary_hardware_type": "nvidia",
            "available_vram_gb": 24.0,
            "has_cuda": True
        }
        model_requirements = {"estimated_vram_gb": 13.5}
        
        result = self.extension_class.optimize_pytorch_for_hardware(
            hardware_info, model_requirements
        )
        assert result["success"]
        assert result["optimization_config"]["device"] == "cuda"
```

## Performance Considerations

1. **Device Selection**: Automatic selection of optimal device (GPU/CPU)
2. **Mixed Precision**: FP16/BF16 for faster inference with minimal quality loss
3. **Quantization**: 8-bit and 4-bit options for memory-constrained environments
4. **Batch Processing**: Optimize batch sizes based on available memory
5. **Model Sharding**: Automatic distribution across multiple GPUs
6. **CPU Offloading**: Offload layers to CPU when GPU memory is limited

## Best Practices

1. **Model Format**: Use safetensors format for faster loading and security
2. **Hardware Matching**: Let the extension auto-detect and optimize for hardware
3. **Quantization Choice**: Use 8-bit for good balance, 4-bit for maximum savings
4. **Memory Management**: Monitor GPU memory usage and adjust configurations
5. **Provider Instances**: Create different instances for different use cases
6. **Error Recovery**: Extension handles OOM errors gracefully with fallback options