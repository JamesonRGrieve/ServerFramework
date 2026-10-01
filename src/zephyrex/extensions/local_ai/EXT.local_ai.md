# Local AI Extension

Local AI extension for AGInfrastructure providing comprehensive AI model capabilities through the Provider Rotation System with advanced hardware optimization and request prioritization.

## Overview

The `local_ai` extension serves as the base framework for local AI model inference, providing:
- **Hardware Detection**: Comprehensive detection and analysis for NVIDIA, AMD, Intel, and Apple Silicon
- **Model Recommendation**: Intelligent model selection based on hardware capabilities and user preferences
- **Request Prioritization**: Subscription-based and role-based request prioritization
- **Model Management**: Download queue management, mounting/unmounting with auto-fit configuration
- **GPU Optimization**: VRAM monitoring, tensor split configuration, and hardware-specific optimizations
- **Provider Coordination**: Programmatic provider management through meta abilities

## Architecture

### Extension Structure
```python
class EXT_Local_AI(AbstractStaticExtension):
    """Base Local AI extension with meta abilities for AI model management."""
    
    name = "local_ai"
    description = "Local AI framework with Provider Rotation System integration"
    
    # Meta abilities - static functionality
    _abilities = {
        "hardware_detection",        # Detect and analyze hardware capabilities
        "model_recommendation",     # Recommend optimal models for hardware
        "download_model",           # Download and cache models
        "queue_download",           # Queue model downloads
        "mount_model",              # Mount models to memory/GPU
        "unmount_model",            # Unmount models from memory
        "query_vram",               # Query GPU VRAM usage
        "auto_fit_model",           # Auto-configure models for hardware
        "prioritize_request",       # Handle request prioritization
        "manage_providers",         # Programmatically manage AI providers
        "configure_tensor_split",   # Configure multi-GPU tensor splitting
    }
```

### Provider Framework Integration
The extension leverages existing Provider framework database tables:

- **Provider**: Model name + parameter size (e.g., "llama3-8b", "mistral-7b")
- **ProviderInstance**: Specific model configurations (quantization, tensor split, context size)
- **ProviderInstanceSettings**: Advanced configuration options (USE_BEAM_SEARCH, CACHE_OFFLOAD, etc.)
- **ProviderInstanceUsage**: Token tracking with keys "input_tokens" and "output_tokens"
- **ProviderInstanceExtensionAbilities**: Controls which AI capabilities are enabled per instance

### Meta Abilities Architecture
The extension provides **meta abilities** (abilities with `meta=True`) that are executed statically by the extension rather than by specific providers. These abilities create and manage AI providers programmatically:

```python
# Providers are created dynamically based on discovered models
discovered_models = EXT_Local_AI.discover_gguf_models()
for model in discovered_models:
    # Creates Provider database record
    provider_record = create_provider(model.name, model.capabilities)
    
    # Creates ProviderInstance with configuration
    instance_record = create_provider_instance(
        provider_id=provider_record.id,
        model_name=model.model_path,
        settings=model.optimal_configuration
    )
```

## Database Entity Roles

### Core Provider System Entities

#### Provider
**Purpose**: Represents an AI model type (e.g., "llama3-8b-gguf", "mistral-7b-pytorch")
**Usage**: Created programmatically when models are discovered
**Key Fields**: `name`, `friendly_name`, `description` (model capabilities)

#### ProviderInstance  
**Purpose**: Specific configuration of a model for a user/team
**Usage**: Represents mounted model instances with specific quantization, tensor split, etc.
**Key Fields**: `provider_id`, `user_id`, `team_id`, `model_name` (path to model file), `enabled`

#### ProviderInstanceSetting
**Purpose**: Advanced configuration options for model instances
**Usage**: Stores settings like USE_BEAM_SEARCH, CACHE_OFFLOAD, ROPE_SCALING, KV_CACHE_QUANT_TYPE
**Key Fields**: `provider_instance_id`, `key`, `value`
**Example Settings**:
```python
{
    "USE_BEAM_SEARCH": "true",
    "CACHE_OFFLOAD": "false", 
    "COMPRESS_CACHE": "true",
    "ROPE_SCALING": '{"type": "linear", "factor": 2.0}',
    "KV_CACHE_QUANT_TYPE": "q4_0",
    "TENSOR_SPLIT": "[0.6, 0.4]"
}
```

#### ProviderInstanceUsage
**Purpose**: Track usage for billing and analytics
**Usage**: Records created after each inference with token counts
**Key Fields**: `provider_instance_id`, `user_id`, `key`, `value`
**Usage Types**:
- `key="input_tokens"`, `value=<token_count>`
- `key="output_tokens"`, `value=<token_count>`

### Extension-Specific Entities

#### ExtensionAbility (with meta=True)
**Purpose**: Represents meta abilities provided by extensions
**Usage**: Abilities like "hardware_detection", "model_recommendation" that are executed by extensions
**Key Fields**: `name`, `extension_id`, `meta=True`

#### ProviderExtensionAbility
**Purpose**: Links providers to specific AI transformation abilities
**Usage**: Defines what AI capabilities each provider supports (text_to_text, text_to_embedding, etc.)
**Key Fields**: `provider_extension_id`, `ability_id`

#### ProviderInstanceExtensionAbility
**Purpose**: Controls which abilities are enabled for specific provider instances
**Usage**: Allows fine-grained control over what each instance can do
**Key Fields**: `provider_instance_id`, `provider_extension_ability_id`, `state`, `forced`

## Hardware Detection and Optimization

### Hardware Detection Engine
```python
class HardwareDetector:
    """Comprehensive hardware detection for AI workloads."""
    
    @staticmethod
    def detect_system_hardware() -> HardwareInfo:
        """Detect system hardware with AI-specific optimizations."""
        # Detects:
        # - NVIDIA CUDA (via nvidia-smi)
        # - AMD ROCm (via rocm-smi) 
        # - Intel GPU (via lspci)
        # - Apple Metal Performance Shaders
        # - OpenCL support
        # - CPU/RAM specifications
```

### Supported Hardware Types
- **NVIDIA**: Full CUDA support with tensor splitting and quantization
- **AMD**: ROCm support with hardware-specific optimizations
- **Intel**: Integrated graphics with OpenCL acceleration
- **Apple**: Metal Performance Shaders for Apple Silicon
- **CPU-Only**: Optimized CPU inference with NUMA support

### Hardware Optimization Features
```python
# Automatic configuration based on detected hardware
optimized_config = ModelRecommendationEngine.auto_configure_for_hardware(
    base_config, hardware_info
)

# Multi-GPU tensor splitting
tensor_split = HardwareDetector.calculate_optimal_tensor_split(gpu_devices)

# VRAM-based optimization
if available_vram < 8:
    config.update({
        "cache_offload": True,
        "compress_cache": True,
        "kv_cache_quant_type": "q4_0"
    })
```

## Model Recommendation System

### Recommendation Engine
```python
class ModelRecommendationEngine:
    """Intelligent model selection based on hardware and preferences."""
    
    @staticmethod
    def get_best_model_for_hardware(
        hardware_info: HardwareInfo,
        task_type: ModelTask,
        preference: ModelPreference,
        available_models: List[Dict]
    ) -> Optional[Dict]:
        """Select optimal model using multi-factor scoring."""
```

### Model Preferences
- **BALANCED**: Considers context window, model size, speed, and quality equally
- **CONTEXT_SIZE**: Prioritizes models with larger context windows
- **MODEL_SIZE**: Prioritizes larger, more capable models
- **SPEED**: Prioritizes faster inference models
- **QUALITY**: Prioritizes highest-quality models based on benchmarks

### Recommendation Factors
```python
# Balanced scoring algorithm
context_score = min(model.context_window / 32768, 1.0) * 25
size_score = min(parameter_count / 70, 1.0) * 25  
speed_score = min((tokens_per_second * hardware_multiplier) / 100, 1.0) * 25
quality_score = min(benchmark_average / 100, 1.0) * 25
total_score = context_score + size_score + speed_score + quality_score
```

## Request Prioritization System

### Priority Levels
```python
class RequestPriority(int, Enum):
    LOWEST = 1
    LOW = 2
    NORMAL = 3
    HIGH = 4
    HIGHEST = 5
    PREMIUM = 6      # Subscription-based
    SYSTEM = 7       # System/admin requests
```

### Prioritization Factors
```python
class RequestPriorityManager:
    @staticmethod
    def calculate_request_priority(
        user_id: Optional[str],
        team_id: Optional[str],
        origin: Optional[str],
        user_roles: Optional[List[str]],
        subscription_tier: Optional[str],  # Via payment extension
        model_type: Optional[str],
        task_type: Optional[ModelTask],
        token_count: Optional[int]
    ) -> RequestPriority:
```

### Subscription Integration
```python
# Optional integration with payment extension
if are_optional_dependencies_met(["payment"]):
    subscription_priority_map = {
        "enterprise": RequestPriority.PREMIUM,
        "pro": RequestPriority.HIGH,
        "basic": RequestPriority.NORMAL,
        "free": RequestPriority.LOW
    }
```

## Advanced Model Features

### Advanced Configuration Options
The extension supports advanced AI model features through ProviderInstanceSettings:

#### Beam Search Configuration
```python
{
    "USE_BEAM_SEARCH": "true",
    "BEAM_WIDTH": "4",
    "LENGTH_PENALTY": "1.0"
}
```

#### Cache Optimization
```python
{
    "CACHE_OFFLOAD": "true",      # Offload cache to system memory
    "COMPRESS_CACHE": "true",     # Enable cache compression
    "CACHE_COMPRESSION_RATIO": "0.5"
}
```

#### RoPE Scaling
```python
{
    "ROPE_SCALING": '{"type": "linear", "factor": 2.0}',
    "ROPE_BASE": "10000.0",
    "ROPE_SCALE": "1.0"
}
```

#### KV Cache Quantization
```python
{
    "KV_CACHE_QUANT_TYPE": "q4_0",  # q4_0, q8_0, fp16, fp32
    "KV_CACHE_PRECISION": "fp16"
}
```

#### Multi-GPU Configuration
```python
{
    "TENSOR_SPLIT": "[0.6, 0.4]",   # Split across 2 GPUs
    "N_GPU_LAYERS": "-1",           # All layers on GPU
    "MAIN_GPU": "0",                # Primary GPU device
    "SPLIT_MODE": "row"             # Tensor split mode
}
```

## API Endpoints

### Hardware and Recommendations
```http
GET /local-ai/hardware
POST /local-ai/recommend-model
GET /local-ai/vram
POST /local-ai/auto-fit
POST /local-ai/tensor-split
```

### Model Management
```http
POST /local-ai/download/queue
GET /local-ai/models/discover
POST /local-ai/models/mount
POST /local-ai/models/unmount
GET /local-ai/models/status/{model_id}
GET /local-ai/models/download-status/{download_id}
```

### Request Processing
```http
POST /local-ai/prioritize
GET /local-ai/preferences
GET /local-ai/tasks
GET /local-ai/health
```

## Integration Patterns

### Provider Creation Flow
1. **Model Discovery**: Extensions scan directories for models
2. **Metadata Extraction**: Analyze model files for capabilities and requirements
3. **Provider Registration**: Create Provider database records
4. **Instance Configuration**: Create ProviderInstance records with optimal settings
5. **Ability Assignment**: Link providers to supported AI transformation abilities

### Inference Request Flow
1. **Request Reception**: API receives inference request
2. **Priority Calculation**: Determine request priority based on user/subscription
3. **Provider Selection**: RotationManager selects optimal provider instance
4. **Configuration Application**: Apply advanced settings from ProviderInstanceSettings
5. **Hardware Optimization**: Auto-configure for detected hardware
6. **Inference Execution**: Execute AI transformation via provider
7. **Usage Tracking**: Record token usage in ProviderInstanceUsage

### Model Lifecycle Management
```python
# Download model
download_result = EXT_Local_AI.queue_download(
    model_id="microsoft/DialoGPT-medium",
    model_format="pytorch",
    priority="high"
)

# Mount model with auto-fit
mount_result = EXT_Local_AI.mount_model(
    provider_instance=instance,
    auto_fit=True,
    tensor_split=[0.7, 0.3]
)

# Execute inference via rotation system
result = RotationManager.rotate(
    "text_to_text",
    prompt="Hello, world!",
    max_tokens=100,
    use_beam_search=True
)

# Unmount when done
unmount_result = EXT_Local_AI.unmount_model(provider_instance=instance)
```

## Dependencies and Requirements

### Required Dependencies
- **local_ai extension**: Base framework (this extension has no dependencies)

### Optional Dependencies
- **payment extension**: For subscription-based request prioritization
  - Enables premium queue positioning
  - Provides subscription tier-based priority levels
  - Integrates with payment customer management

### Hardware Dependencies
- **NVIDIA GPUs**: CUDA toolkit and drivers
- **AMD GPUs**: ROCm toolkit and drivers  
- **Intel GPUs**: OpenCL drivers
- **Apple Silicon**: Metal Performance Shaders (built-in)

## Configuration Management

### Environment Variables
- `MODELS_DIR`: Base directory for model storage
- `LOCAL_AI_MAX_MEMORY_GB`: Maximum memory usage limit
- `LOCAL_AI_AUTO_DOWNLOAD`: Enable automatic model downloading
- `LOCAL_AI_PRIORITIZE_CONTEXT`: Prefer models with larger context windows
- `LOCAL_AI_PRIORITIZE_QUALITY`: Prefer higher-quality models
- `LOCAL_AI_PRIORITIZE_SPEED`: Prefer faster models

### Provider Instance Configuration
```python
# Create provider instance with configuration
config = LocalAIProviderConfiguration(
    model_path="/models/llama3-8b.gguf",
    context_size=8192,
    use_beam_search=True,
    cache_offload=True,
    compress_cache=True,
    rope_scaling={"type": "linear", "factor": 2.0},
    kv_cache_quant_type="q4_0",
    tensor_split=[0.6, 0.4],
    n_gpu_layers=-1
)

# Convert to ProviderInstanceSettings
settings = config.to_provider_settings()
```

## Testing and Validation

### Unit Tests
- Hardware detection accuracy
- Model recommendation algorithms
- Priority calculation logic
- Configuration optimization

### Integration Tests
- Provider creation from discovered models
- ProviderInstanceSetting management
- Usage tracking functionality
- Request routing through rotation system

### Performance Tests
- Hardware detection speed
- Model recommendation performance
- VRAM usage optimization
- Multi-GPU tensor splitting effectiveness

## Best Practices

### Performance Optimization
1. **Hardware Detection**: Cache hardware info to avoid repeated detection
2. **Model Loading**: Use lazy loading for models not actively in use
3. **Memory Management**: Monitor VRAM usage and automatically offload when needed
4. **Request Batching**: Batch compatible requests for improved throughput

### Security Considerations
1. **Model Validation**: Validate model files before loading
2. **Resource Limits**: Enforce memory and compute limits per user/team
3. **Access Control**: Use ProviderInstanceExtensionAbilities for fine-grained permissions
4. **Usage Monitoring**: Track and limit resource usage per subscription tier

### Scalability Patterns
1. **Provider Instances**: Create separate instances for different user groups
2. **Load Balancing**: Use rotation system for distributing load across instances
3. **Auto-scaling**: Automatically mount/unmount models based on demand
4. **Resource Pooling**: Share GPU resources across compatible model instances

## Future Enhancements

### Planned Features
- **Model Quantization**: Dynamic quantization based on available resources
- **Distributed Inference**: Multi-node model execution
- **Model Serving**: Persistent model servers with API endpoints
- **Performance Analytics**: Detailed performance metrics and optimization suggestions
- **Custom Model Support**: Support for custom model formats and architectures

### Integration Opportunities
- **Monitoring Extensions**: Integration with metrics and logging systems
- **Storage Extensions**: Support for cloud-based model storage
- **Security Extensions**: Enhanced authentication and authorization
- **Analytics Extensions**: Advanced usage analytics and billing integration