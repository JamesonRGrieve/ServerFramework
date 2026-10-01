# Local AI GGUF Extension

GGUF model extension for AGInfrastructure providing quantized model support through the Provider Rotation System via llama.cpp integration.

## Overview

The `local_ai_gguf` extension extends the Local AI framework to provide GGUF model support through:
- **Automatic Model Discovery**: Scans directories for GGUF models and creates Provider records
- **Dynamic Provider Creation**: Creates providers for each discovered GGUF model
- **Hardware Optimization**: Automatic optimization for CPU and GPU configurations
- **Quantization Support**: Full support for different quantization levels (Q4, Q5, Q8, etc.)
- **Memory Efficiency**: Optimized memory usage for large language models
- **Provider Rotation Integration**: Seamless integration with the Provider Rotation System

## Architecture

### Extension Structure
```python
class EXT_Local_AI_GGUF(AbstractStaticExtension):
    """GGUF model extension providing meta abilities for model management."""
    
    name: ClassVar[str] = "local_ai_gguf"
    dependencies: ClassVar[Dependencies] = Dependencies([
        EXT_Dependency("local_ai", is_required=True),
        PIP_Dependency("llama-cpp-python", semver=">=0.2.0")
    ])
    
    # Meta abilities for model management
    _abilities: ClassVar[set] = {
        "discover_gguf_models",
        "download_gguf_model",
        "mount_gguf_model",
        "unmount_gguf_model",
        "configure_gguf_settings",
        "optimize_gguf_for_hardware",
    }
```

### Provider Implementation
```python
class PRV_GGUF_Model(AbstractAIProvider):
    """Concrete GGUF model provider for AI inference."""
    
    extension_type: ClassVar[str] = "local_ai_gguf"
    
    # AI transformation abilities
    _abilities: ClassVar[set] = {
        "text_to_text",
        "text_to_embedding",
    }
    
    @classmethod
    def bond_instance(cls, instance_data: Dict[str, Any]) -> bool:
        """Load GGUF model into memory."""
        # Loads model using llama-cpp-python
        
    @ability
    @classmethod
    def text_to_text(cls, prompt: str, **kwargs) -> Dict[str, Any]:
        """Generate text using GGUF model."""
```

## Model Discovery and Registration

### Automatic Model Discovery
```python
# Extension discovers GGUF models and creates Provider metadata
result = EXT_Local_AI_GGUF.discover_gguf_models(
    search_paths=[
        "./models/gguf",
        "~/.cache/huggingface/hub"
    ]
)

# Returns discovered models with metadata
{
    "success": True,
    "discovered_models": [
        {
            "name": "llama-2-7b-chat",
            "file_path": "/models/gguf/llama-2-7b-chat.Q4_K_M.gguf",
            "file_size_gb": 3.9,
            "estimated_vram_gb": 4.5,
            "quantization": "Q4_K_M",
            "capabilities": ["text_to_text"],
            "context_window": 4096
        }
    ],
    "count": 1
}
```

### Provider Registration Flow
1. Extension discovers GGUF models in configured directories
2. For each model, creates a Provider record with appropriate metadata
3. Provider instances represent specific configurations (GPU layers, context size)
4. Providers are available through the rotation system

## Configuration

### Environment Variables
```python
_env: ClassVar[Dict[str, Any]] = {
    "GGUF_MODELS_DIR": {
        "type": str,
        "default": "./models/gguf",
        "description": "Directory to store GGUF models",
    },
    "GGUF_AUTO_DISCOVER": {
        "type": bool,
        "default": True,
        "description": "Automatically discover GGUF models on startup",
    },
    "GGUF_MAX_CONTEXT": {
        "type": int,
        "default": 4096,
        "description": "Default maximum context window",
    },
    "GGUF_DEFAULT_QUANT": {
        "type": str,
        "default": "Q4_K_M",
        "description": "Default quantization type",
    },
}
```

### Advanced Settings
Provider instances can be configured with advanced settings:

```python
# Configure advanced GGUF settings
result = EXT_Local_AI_GGUF.configure_gguf_settings(
    provider_instance,
    settings={
        "USE_BEAM_SEARCH": "true",
        "CACHE_OFFLOAD": "true",
        "KV_CACHE_QUANT_TYPE": "q4_0",
        "N_GPU_LAYERS": "32",
        "TENSOR_SPLIT": "[0.5, 0.5]",  # Multi-GPU
    }
)
```

## Hardware Optimization

### Automatic Hardware Detection and Optimization
```python
# Get hardware info from local_ai extension
hardware_info = EXT_Local_AI.hardware_detection()

# Optimize GGUF configuration for hardware
optimization_result = EXT_Local_AI_GGUF.optimize_gguf_for_hardware(
    hardware_info,
    model_requirements={"estimated_vram_gb": 4.5}
)

# Returns optimized configuration
{
    "success": True,
    "optimization_config": {
        "hardware_type": "nvidia",
        "n_gpu_layers": -1,  # All layers on GPU
        "main_gpu": 0,
        "use_mmap": True,
        "cache_offload": False,
        "kv_cache_quant_type": "q8_0"
    }
}
```

### Hardware-Specific Optimizations
- **NVIDIA**: Full GPU offloading with memory optimizations
- **Apple Silicon**: Limited MPS support with optimized settings
- **AMD**: ROCm support with GPU acceleration
- **CPU**: Thread optimization and memory mapping

## Usage via Provider Rotation

### Text Generation
```python
from rotation.RotationManager import RotationManager

# Generate text using rotation system
result = RotationManager.rotate(
    "text_to_text",
    prompt="What is the capital of France?",
    max_tokens=100,
    temperature=0.7,
    provider_filter={"extension_type": "local_ai_gguf"}
)

print(result["text"])
```

### Embedding Generation
```python
# Generate embeddings
result = RotationManager.rotate(
    "text_to_embedding",
    text="Hello, world!",
    normalize=True,
    provider_filter={"extension_type": "local_ai_gguf"}
)

embeddings = result["embedding"]
```

## Model Management

### Download Models
```python
# Download a GGUF model
result = EXT_Local_AI_GGUF.download_gguf_model(
    model_id="TheBloke/Llama-2-7B-Chat-GGUF",
    quantization="Q4_K_M"
)

# Returns download status
{
    "success": True,
    "download_id": "gguf_TheBloke/Llama-2-7B-Chat-GGUF_Q4_K_M_1234",
    "status": "accepted",
    "estimated_size_gb": 3.9
}
```

### Mount/Unmount Models
```python
# Mount model to GPU/memory
mount_result = EXT_Local_AI_GGUF.mount_gguf_model(
    provider_instance,
    estimated_vram_gb=4.5
)

# Unmount when done
unmount_result = EXT_Local_AI_GGUF.unmount_gguf_model(
    provider_instance
)
```

## Quantization Support

Supported GGUF quantization formats:

| Quantization | Memory Reduction | Quality | Recommended Use |
|--------------|------------------|---------|-----------------|
| Q4_K_M | 75% | Medium | Balanced performance and quality |
| Q4_K_S | 77% | Medium-Low | Maximum compression |
| Q5_K_M | 69% | Medium-High | Better quality than Q4 |
| Q5_K_S | 71% | Medium | Smaller Q5 variant |
| Q8_0 | 50% | High | Highest quality |
| F16 | 0% | Full | No quantization |

## Integration with Payment Extension

When payment extension is available, supports subscription-based prioritization:

```python
# High-priority requests for premium users
priority_config = {
    "level": "high",
    "high_priority": True,
    "queue_position": -1
}

# Standard requests queue normally
priority_config = {
    "level": "standard",
    "high_priority": False,
    "queue_position": 0
}
```

## Testing

```python
class TestGGUFExtension(AbstractEXTTest):
    extension_class = EXT_Local_AI_GGUF
    
    def test_model_discovery(self):
        """Test GGUF model discovery."""
        result = self.extension_class.discover_gguf_models()
        assert result["success"]
        assert "discovered_models" in result
    
    def test_hardware_optimization(self):
        """Test hardware optimization."""
        hardware_info = {
            "primary_hardware_type": "nvidia",
            "available_vram_gb": 8.0
        }
        model_requirements = {"estimated_vram_gb": 4.0}
        
        result = self.extension_class.optimize_gguf_for_hardware(
            hardware_info, model_requirements
        )
        assert result["success"]
        assert result["optimization_config"]["n_gpu_layers"] == -1
```

## Performance Considerations

1. **Memory Management**: Uses mmap and mlock for efficient memory usage
2. **GPU Utilization**: Automatic layer distribution based on available VRAM
3. **CPU Optimization**: Thread count optimization for CPU inference
4. **Quantization**: Choose appropriate quantization for quality/performance balance
5. **Context Window**: Configure based on use case and available memory
6. **Batch Processing**: Adjust batch size for optimal throughput

## Best Practices

1. **Model Selection**: Choose quantization based on quality/performance requirements
2. **Hardware Detection**: Use automatic hardware optimization for best performance
3. **Memory Monitoring**: Monitor VRAM usage and adjust GPU layers if needed
4. **Provider Instances**: Create multiple instances with different configurations
5. **Error Handling**: Extension provides detailed error messages for troubleshooting
6. **Resource Management**: Models are automatically cleaned up on unmount