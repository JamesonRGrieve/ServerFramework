# AI Prompts Extension

AI prompt management extension for AGInfrastructure providing comprehensive prompt template creation, optimization, and AI model integration through the AI framework.

## Overview

The `ai_prompts` extension provides advanced prompt engineering capabilities, building on the AI framework to create, optimize, and manage AI prompts. Includes template systems, A/B testing, prompt optimization, and integration with multiple AI providers through the Provider Rotation System.

## Architecture

### Extension Structure
```python
class EXT_AI_Prompts(AbstractStaticExtension):
    """AI prompt management extension with AI framework integration."""
    
    name: ClassVar[str] = "ai_prompts"
    friendly_name: ClassVar[str] = "AI Prompt Management"
    dependencies: ClassVar[Dependencies] = Dependencies([
        EXT_Dependency(name="ai", reason="Core AI provider functionality")
    ])
    
    # Meta abilities for prompt management
    _abilities: ClassVar[set] = {
        "manage_prompt_templates",
        "optimize_prompts",
        "track_prompt_analytics",
        "manage_prompt_versions",
    }
```

### Prompt Provider Implementation
```python
from extensions.ai_prompts.EXT_AI_Prompts import AbstractAIPromptProvider
from extensions.AbstractExtensionProvider import ability

class PRV_Template_AIPrompts(AbstractAIPromptProvider):
    """Template-based prompt provider with AI framework integration."""
    
    name: ClassVar[str] = "template"
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return TemplateProviderInstance(instance)
    
    @classmethod
    def render_template(cls, bonded_instance: AbstractProviderInstance, template: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Render a prompt template with variables."""
        # Implementation uses bonded_instance for API operations
        pass
    
    @classmethod
    def validate_prompt(cls, bonded_instance: AbstractProviderInstance, prompt: str) -> Dict[str, Any]:
        """Validate a prompt for correctness and effectiveness."""
        # Implementation uses bonded_instance for validation
        pass
    
    @classmethod
    def generate_variations(cls, bonded_instance: AbstractProviderInstance, base_prompt: str, count: int = 3) -> Dict[str, Any]:
        """Generate variations of a base prompt."""
        # Implementation uses bonded_instance to generate variations
        pass
```

## Core Capabilities

### Prompt Template Management
```python
# Meta ability: Manage prompt templates
result = EXT_AI_Prompts.manage_prompt_templates()

# Through provider: Create dynamic prompt template
from extensions.ai_prompts.providers import TemplateProvider

result = TemplateProvider.render_template(
    template="Review this {{language}} code for {{criteria}}:\n\n{{code}}\n\nProvide specific feedback on:",
    variables={
        "language": "Python",
        "criteria": "performance",
        "code": "def sort_list(items): return sorted(items)"
    }
)
```

### Prompt Optimization
```python
# Meta ability: Optimize prompts
result = EXT_AI_Prompts.optimize_prompts(
    template="Write a function that sorts a list",
    optimization_goal="clarity"
)

# Through provider: Generate prompt variations
from extensions.ai_prompts.providers import OptimizationProvider

variations = OptimizationProvider.generate_variations(
    base_prompt="Write a function that sorts a list",
    variation_count=5,
    variation_goals=["clarity", "specificity", "brevity"]
)
```

### AI Model Integration
```python
# Through AI extension for cross-provider testing
from extensions.ai.EXT_AI import EXT_AI

# Test prompt across multiple AI providers
for provider in EXT_AI.get_providers():
    result = provider.text_to_text(
        prompt="Explain quantum computing in simple terms",
        model_preferences={"complexity": "low"}
    )

## Prompt Template System

### Template Engine
Built on Jinja2 with AI-specific extensions:

```python
# Advanced template with AI-specific filters
template = """
You are a {{role}} assistant.

Context ({{context|token_limit(500)}}):
{{context}}

User Request:
{{user_request}}

Instructions:
{{instructions|format_code("markdown")}}

Response format: {{format}}
"""
```

### Template Categories
- **System Prompts**: Role definitions and behavior guidelines
- **User Prompts**: Query templates and instruction formats
- **Assistant Prompts**: Response templates and format guides
- **Function Prompts**: API function descriptions and parameters
- **Chain Prompts**: Multi-step workflow templates

### Dynamic Template Rendering
```python
# Render template with context-aware variables
result = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.render_template,
    template_name="technical_explanation",
    variables={
        "topic": "microservices",
        "audience": "senior developers",
        "depth": "detailed",
        "examples": True
    },
    context_enhancement=True
)
```

## Prompt Optimization

### Optimization Strategies
- **Clarity Enhancement**: Remove ambiguity and improve instructions
- **Token Efficiency**: Reduce token usage while maintaining quality
- **Specificity Improvement**: Add relevant constraints and examples
- **Context Optimization**: Balance context length with relevance
- **Model-Specific Tuning**: Optimize for specific AI models

### A/B Testing Framework
```python
# A/B test prompt variations
result = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.ab_test_prompts,
    prompt_variants=[
        {"name": "direct", "template": "Write a {{type}} function for {{task}}"},
        {"name": "detailed", "template": "Create a well-documented {{type}} function that {{task}}. Include error handling and type hints."}
    ],
    test_inputs=[
        {"type": "Python", "task": "sorting a list"},
        {"type": "JavaScript", "task": "validating email"}
    ],
    success_metrics=["code_quality", "documentation", "error_handling"]
)
```

### Performance Analytics
```python
# Analyze prompt performance across models
analytics = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.analyze_performance,
    prompt_id="prompt_123",
    time_range="last_30_days",
    metrics=[
        "response_quality",
        "token_efficiency", 
        "success_rate",
        "user_satisfaction",
        "cost_effectiveness"
    ]
)
```

## Context Generation

### Intelligent Context Enhancement
```python
# Generate contextual information for prompts
result = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.generate_context,
    query="How to implement JWT authentication",
    context_types=["technical_background", "code_examples", "best_practices"],
    domain="web_development",
    complexity_level="intermediate"
)
```

### Context Optimization
```python
# Optimize context for token efficiency
result = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.optimize_context,
    original_context="Long technical documentation...",
    target_length=500,
    preservation_priorities=["key_concepts", "examples", "warnings"],
    compression_method="intelligent_summarization"
)
```

## Prompt Engineering Tools

### Prompt Validation
```python
# Validate prompt structure and safety
validation = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.validate_prompt,
    prompt="Create a user authentication system",
    validation_checks=[
        "instruction_clarity",
        "safety_guidelines",
        "token_efficiency",
        "completion_likelihood"
    ]
)
```

### Chain Prompt Management
```python
# Create multi-step prompt chains
chain = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.create_prompt_chain,
    name="Code Generation Chain",
    steps=[
        {"step": "analyze", "template": "Analyze requirements: {{requirements}}"},
        {"step": "design", "template": "Design solution based on analysis: {{analysis_result}}"},
        {"step": "implement", "template": "Implement the design: {{design_result}}"},
        {"step": "test", "template": "Create tests for: {{implementation_result}}"}
    ],
    chain_variables=["requirements"]
)
```

## Database Schema

### Core Tables
- **PromptTemplates**: Template definitions and metadata
- **PromptVariables**: Template variable definitions
- **PromptVersions**: Template version history
- **PromptTests**: Testing results and performance data
- **PromptAnalytics**: Usage analytics and optimization data
- **PromptChains**: Multi-step prompt workflows

### Prompt Models
```python
class PromptTemplateModel:
    """Prompt template entity."""
    id: str
    name: str
    template_content: str
    variables: List[str]
    category: str
    optimization_level: int
    created_at: datetime
    updated_at: datetime
    
class PromptTestModel:
    """Prompt test results."""
    id: str
    template_id: str
    test_input: Dict[str, Any]
    ai_provider: str
    model_id: str
    response_quality: float
    token_usage: int
    response_time_ms: int
    success: bool
    created_at: datetime
```

## Advanced Features

### Prompt Versioning
```python
# Version control for prompt templates
result = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.create_version,
    template_id="template_123",
    changes="Improved clarity and added examples",
    version_type="minor",
    auto_test=True
)
```

### Template Inheritance
```python
# Create prompt templates with inheritance
result = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.create_inherited_template,
    parent_template="base_code_template",
    name="Python Code Template",
    overrides={
        "language": "Python",
        "style_guide": "PEP 8",
        "additional_instructions": "Include type hints and docstrings"
    }
)
```

### Collaborative Prompt Development
```python
# Collaborate on prompt development
result = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.create_collaboration,
    template_id="template_123",
    collaborators=["user_456", "user_789"],
    permissions=["edit", "test", "comment"],
    review_required=True
)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_PROMPTS_TEMPLATE_CACHE_SIZE` | `1000` | Template cache size |
| `AI_PROMPTS_DEFAULT_OPTIMIZATION` | `clarity` | Default optimization goal |
| `AI_PROMPTS_MAX_TEMPLATE_SIZE` | `10000` | Maximum template size in characters |
| `AI_PROMPTS_AUTO_OPTIMIZE` | `false` | Enable automatic optimization |
| `AI_PROMPTS_TEST_TIMEOUT` | `30` | Test timeout in seconds |
| `AI_PROMPTS_ANALYTICS_ENABLED` | `true` | Enable analytics tracking |
| `AI_PROMPTS_VERSION_LIMIT` | `50` | Maximum versions per template |

## Usage Examples

### Creating and Testing Prompts
```python
# Create a comprehensive prompt template
template = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.create_template,
    name="API Documentation Generator",
    template="""
Generate comprehensive API documentation for the following {{api_type}} endpoint:

Endpoint: {{endpoint}}
Method: {{method}}
Purpose: {{purpose}}

Include:
- Clear description
- Parameter details
- Response format
- Example request/response
- Error codes

Format: {{output_format}}
""",
    variables=["api_type", "endpoint", "method", "purpose", "output_format"],
    optimization_goals=["completeness", "clarity"]
)

# Test the template with multiple scenarios
test_results = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.test_template,
    template_id=template.template_id,
    test_cases=[
        {
            "api_type": "REST",
            "endpoint": "/users/{id}",
            "method": "GET",
            "purpose": "Retrieve user information",
            "output_format": "Markdown"
        },
        {
            "api_type": "GraphQL",
            "endpoint": "/graphql",
            "method": "POST", 
            "purpose": "Query user data",
            "output_format": "OpenAPI"
        }
    ]
)
```

### Prompt Optimization Workflow
```python
# Comprehensive prompt optimization
optimization = await EXT_AI_Prompts.root.rotate(
    EXT_AI_Prompts.optimize_workflow,
    original_prompt="Write code for user authentication",
    optimization_pipeline=[
        {"step": "clarity", "weight": 0.3},
        {"step": "specificity", "weight": 0.4},
        {"step": "token_efficiency", "weight": 0.3}
    ],
    target_models=["gpt-4", "claude-3-sonnet"],
    validation_criteria={
        "min_quality_score": 0.8,
        "max_token_increase": 0.2,
        "success_rate_threshold": 0.9
    }
)
```

## Testing

### AI Prompts Extension Testing
```python
class TestAIPromptsExtension(AbstractEXTTest):
    extension_class = EXT_AI_Prompts
    
    def test_meta_abilities(self, extension_server, extension_db):
        """Test extension meta abilities."""
        # Test template management
        result = EXT_AI_Prompts.manage_prompt_templates()
        assert result["success"]
        
        # Test optimization capability
        result = EXT_AI_Prompts.optimize_prompts(
            template="Write a function",
            optimization_goal="clarity"
        )
        assert result["success"]
    
    def test_provider_abilities(self, extension_server, extension_db):
        """Test provider abilities."""
        from extensions.ai_prompts.providers import TemplateProvider
        
        # Test template rendering
        result = TemplateProvider.render_template(
            template="Hello {{name}}",
            variables={"name": "World"}
        )
        assert result == "Hello World"
        
        # Test prompt validation
        validation = TemplateProvider.validate_prompt(
            prompt="Write a function"
        )
        assert validation["is_valid"]
```

## Performance Considerations

1. **Template Caching**: Cache rendered templates for repeated use
2. **Optimization Batching**: Batch optimization requests for efficiency
3. **Token Usage Monitoring**: Track and optimize token consumption
4. **Test Result Storage**: Efficiently store and retrieve test results
5. **Context Processing**: Optimize context generation and compression
6. **Version Management**: Efficient storage of template versions

## Best Practices

1. **Template Design**: Create modular, reusable prompt templates
2. **Optimization Strategy**: Balance quality, efficiency, and cost
3. **Testing Methodology**: Comprehensive testing across providers and scenarios
4. **Version Control**: Systematic versioning and change tracking
5. **Performance Monitoring**: Regular analysis of prompt effectiveness
6. **Collaboration**: Structured collaborative prompt development
7. **Safety Guidelines**: Implement prompt safety and content filters
8. **Documentation**: Comprehensive template documentation and examples