# AI Extension

Core AI framework extension for AGInfrastructure providing foundational AI capabilities and provider integration.

## Overview

The `ai` extension serves as the foundational AI framework for AGInfrastructure, providing core AI capabilities, provider management, and the base infrastructure for AI-dependent extensions. Built on the Provider Rotation System for scalability, failover, and load balancing across multiple AI providers.

## Architecture

### Extension Structure
```python
class EXT_AI(AbstractStaticExtension):
    """Core AI framework extension with provider rotation."""
    
    name = "ai"
    friendly_name = "AI Framework"
    
    @classproperty
    def providers(cls) -> List[Type]:
        """Auto-discover AI providers in this extension."""
        return cls._providers
```

### Base Provider Implementation
```python
from extensions.ai.EXT_AI import AbstractAIExtensionProvider

class OpenAI_AIProvider(AbstractAIExtensionProvider):
    """OpenAI provider for AI operations."""
    
    name = "openai"
    extension = EXT_AI
    
    @classmethod
    def get_abilities(cls) -> Set[str]:
        return {
            "text_generation",
            "chat_completion",
            "embedding_generation",
            "image_generation",
            "speech_to_text",
            "text_to_speech"
        }
```

## Core Capabilities

### Text Generation
```python
# Generate text using provider rotation
result = await EXT_AI.root.rotate(
    EXT_AI.generate_text,
    prompt="Explain quantum computing",
    max_tokens=500,
    temperature=0.7
)
```

### Chat Completion
```python
# Chat completion with conversation context
result = await EXT_AI.root.rotate(
    EXT_AI.chat_completion,
    messages=[
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "What is machine learning?"}
    ],
    model="gpt-4"
)
```

### Embedding Generation
```python
# Generate embeddings for semantic search
result = await EXT_AI.root.rotate(
    EXT_AI.generate_embeddings,
    text="AGInfrastructure is a comprehensive server framework",
    model="text-embedding-ada-002"
)
```

## AI Provider Framework

### Provider Types
- **Cloud Providers**: OpenAI, Anthropic, Google AI, Azure OpenAI
- **Local Providers**: HuggingFace Transformers, Ollama, LocalAI
- **Specialized Providers**: Image generation, speech processing, embeddings

### Provider Configuration
```python
class AIProviderConfig:
    """Configuration for AI providers."""
    
    api_key: str
    base_url: Optional[str]
    model_mappings: Dict[str, str]
    rate_limits: Dict[str, int]
    retry_config: RetryConfig
    timeout_seconds: int
```

### Provider Rotation System
```python
# Automatic failover between providers
config = {
    "primary": "openai",
    "fallback": ["anthropic", "local"],
    "load_balancing": True,
    "health_check": True
}

result = await EXT_AI.root.rotate(
    EXT_AI.generate_text,
    prompt="Hello world",
    provider_config=config
)
```

## Model Management

### Model Registry
```python
class ModelRegistry:
    """Central registry for AI models."""
    
    @classmethod
    def register_model(cls, provider: str, model_id: str, capabilities: Set[str]):
        """Register a model with its capabilities."""
        pass
        
    @classmethod
    def get_models_by_capability(cls, capability: str) -> List[str]:
        """Get models that support a specific capability."""
        pass
```

### Model Selection
```python
# Automatic model selection based on capabilities
result = await EXT_AI.root.rotate(
    EXT_AI.auto_generate,
    task="text_generation",
    requirements={"max_tokens": 2000, "multimodal": False},
    content="Write a technical blog post about microservices"
)
```

## Provider Implementations

### OpenAI Provider
```python
class OpenAI_AIProvider(AbstractAIExtensionProvider):
    """OpenAI integration with full API support."""
    
    supported_models = [
        "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo",
        "text-embedding-ada-002", "dall-e-3"
    ]
    
    @classmethod
    async def generate_text(cls, prompt: str, **kwargs) -> str:
        # OpenAI API implementation
        pass
```

### Anthropic Provider
```python
class Anthropic_AIProvider(AbstractAIExtensionProvider):
    """Anthropic Claude integration."""
    
    supported_models = [
        "claude-3-opus", "claude-3-sonnet", "claude-3-haiku"
    ]
    
    @classmethod
    async def chat_completion(cls, messages: List[Dict], **kwargs) -> str:
        # Anthropic API implementation
        pass
```

### Local Provider
```python
class Local_AIProvider(AbstractAIExtensionProvider):
    """Local model integration."""
    
    @classmethod
    def can_handle_model(cls, model_path: str) -> bool:
        """Check if local model can be loaded."""
        pass
```

## Streaming and Real-time

### Streaming Text Generation
```python
# Stream text generation token by token
async for token in EXT_AI.root.stream(
    EXT_AI.stream_text,
    prompt="Write a story about AI",
    stream=True
):
    print(token, end="", flush=True)
```

### Real-time Processing
```python
# Real-time AI processing pipeline
pipeline = await EXT_AI.root.create_pipeline([
    ("preprocess", EXT_AI.preprocess_text),
    ("generate", EXT_AI.generate_text),
    ("postprocess", EXT_AI.postprocess_output)
])

result = await pipeline.process(input_data)
```

## Error Handling and Resilience

### Retry Configuration
```python
retry_config = {
    "max_retries": 3,
    "backoff_factor": 2,
    "retry_on": ["rate_limit", "timeout", "server_error"],
    "fallback_providers": ["anthropic", "local"]
}
```

### Health Monitoring
```python
# Provider health checking
health_status = await EXT_AI.root.check_health()
for provider, status in health_status.items():
    print(f"{provider}: {status.status} ({status.response_time}ms)")
```

## Database Schema

### Core Tables
- **AIProviders**: Registered AI provider configurations
- **AIModels**: Available models and their capabilities  
- **AIRequestLogs**: Request history and performance metrics
- **AIProviderHealth**: Provider status and health metrics

### Configuration Models
```python
class AIProviderModel:
    """AI provider configuration."""
    id: str
    name: str
    provider_type: str
    api_endpoint: str
    capabilities: List[str]
    config: Dict[str, Any]
    
class AIRequestModel:
    """AI request logging."""
    id: str
    provider_id: str
    model_id: str
    request_type: str
    tokens_used: int
    response_time_ms: int
    success: bool
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_PRIMARY_PROVIDER` | `openai` | Default AI provider |
| `AI_FALLBACK_PROVIDERS` | `anthropic,local` | Comma-separated fallback providers |
| `AI_REQUEST_TIMEOUT` | `30` | Request timeout in seconds |
| `AI_MAX_RETRIES` | `3` | Maximum retry attempts |
| `AI_RATE_LIMIT_BUFFER` | `0.1` | Rate limit buffer (10%) |
| `AI_ENABLE_CACHING` | `true` | Enable response caching |
| `AI_CACHE_TTL` | `3600` | Cache time-to-live in seconds |
| `OPENAI_API_KEY` | None | OpenAI API key |
| `ANTHROPIC_API_KEY` | None | Anthropic API key |

## Usage Examples

### Basic Text Generation
```python
from extensions.ai.EXT_AI import EXT_AI

# Simple text generation
response = await EXT_AI.root.rotate(
    EXT_AI.generate_text,
    prompt="Explain the benefits of microservices architecture",
    max_tokens=300,
    temperature=0.7
)

print(response.text)
```

### Multi-Provider Configuration
```python
# Configure multiple providers with preferences
providers = {
    "text_generation": ["openai", "anthropic"],
    "embeddings": ["openai", "local"],
    "image_generation": ["openai", "local"]
}

result = await EXT_AI.root.rotate(
    EXT_AI.generate_text,
    prompt="Hello world",
    preferred_providers=providers["text_generation"]
)
```

### Advanced Model Selection
```python
# Context-aware model selection
context = {
    "task_type": "code_generation",
    "complexity": "high",
    "domain": "python",
    "max_cost": 0.05
}

result = await EXT_AI.root.smart_rotate(
    EXT_AI.generate_code,
    prompt="Create a FastAPI endpoint for user authentication",
    context=context
)
```

## Testing

### AI Extension Testing
```python
class TestAIExtension(AbstractEXTTest):
    extension_class = EXT_AI
    
    def test_ai_provider_discovery(self, extension_server, extension_db):
        """Test AI provider discovery."""
        providers = self.extension_class.providers()
        assert len(providers) > 0
        
        openai_provider = next((p for p in providers if p.name == "openai"), None)
        assert openai_provider is not None
        
        abilities = openai_provider.get_abilities()
        assert "text_generation" in abilities
        assert "chat_completion" in abilities
    
    async def test_text_generation(self, extension_server, extension_db):
        """Test text generation functionality."""
        result = await EXT_AI.root.rotate(
            EXT_AI.generate_text,
            prompt="Test prompt",
            max_tokens=50
        )
        
        assert result.success
        assert len(result.text) > 0
```

## Performance Considerations

1. **Provider Selection**: Optimize provider choice based on cost, speed, and quality
2. **Caching Strategy**: Cache responses for repeated queries
3. **Rate Limiting**: Respect provider rate limits and implement backoff
4. **Token Management**: Monitor token usage and costs
5. **Batching**: Batch requests when possible for efficiency
6. **Health Monitoring**: Continuously monitor provider health and performance

## Best Practices

1. **Provider Diversity**: Use multiple providers for resilience
2. **Cost Optimization**: Monitor and optimize AI usage costs
3. **Security**: Secure API keys and implement access controls
4. **Monitoring**: Track performance metrics and error rates
5. **Fallback Strategy**: Always have local fallback options
6. **Model Selection**: Choose appropriate models for each task
7. **Error Handling**: Implement robust error handling and recovery
8. **Testing**: Thoroughly test with different providers and scenarios