# AI Agents Extension

AI agent management extension for AGInfrastructure providing intelligent agent creation, configuration, and activity tracking through the AI framework.

## Overview

The `ai_agents` extension provides comprehensive AI agent management capabilities, building on the AI framework to create intelligent agents that can participate in conversations, execute tasks, and interact with users. Built on the Provider Rotation System for scalable agent operations.

## Architecture

### Extension Structure
```python
class EXT_AI_Agents(AbstractStaticExtension):
    """AI agent management extension with AI framework integration."""
    
    name = "ai_agents"
    friendly_name = "AI Agent Management"
    dependencies = ["ai"]  # Depends on AI framework
    
    @classproperty
    def providers(cls) -> List[Type]:
        """Auto-discover AI agent providers in this extension."""
        return cls._providers
```

### Agent Provider Implementation
```python
from extensions.ai_agents.EXT_AI_Agents import AbstractAIAgentExtensionProvider
from extensions.ai.EXT_AI import AbstractAIExtensionProvider

class Conversational_AIAgentProvider(AbstractAIAgentExtensionProvider):
    """Conversational agent provider with AI framework integration."""
    
    name = "conversational"
    extension = EXT_AI_Agents
    
    @classmethod
    def get_abilities(cls) -> Set[str]:
        return {
            "conversation_participation",
            "context_management", 
            "personality_adaptation",
            "task_execution",
            "knowledge_retrieval"
        }
```

## Core Capabilities

### Agent Creation and Management
```python
# Create intelligent agent with AI provider
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.create_agent,
    name="Technical Assistant",
    personality="helpful and knowledgeable",
    ai_provider="openai",
    model="gpt-4",
    abilities=["conversation", "code_generation", "research"]
)
```

### Agent Configuration
```python
# Configure agent behavior and capabilities
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.configure_agent,
    agent_id="agent_123",
    system_prompt="You are a helpful technical assistant specializing in Python development",
    temperature=0.7,
    max_tokens=1000,
    conversation_memory=10
)
```

### Agent Activities
```python
# Track agent activity in conversations
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.create_activity,
    agent_id="agent_123",
    activity_type="code_generation",
    conversation_id="conv_456",
    input_data={"request": "Create a FastAPI endpoint"},
    output_data={"code": "generated_code_here"}
)
```

## Agent Framework

### Agent Types
- **Conversational Agents**: General conversation and assistance
- **Task Agents**: Specialized task execution and automation
- **Knowledge Agents**: Information retrieval and research
- **Creative Agents**: Content creation and ideation
- **Code Agents**: Programming assistance and code generation

### Agent Personality System
```python
class AgentPersonality:
    """Agent personality configuration."""
    
    communication_style: str  # "formal", "casual", "technical"
    knowledge_domain: List[str]  # ["programming", "science", "business"]
    response_length: str  # "concise", "detailed", "adaptive"
    creativity_level: float  # 0.0 to 1.0
    helpfulness_bias: float  # 0.0 to 1.0
```

### Agent Memory System
```python
class AgentMemory:
    """Agent conversation memory management."""
    
    short_term_memory: int  # Recent messages to remember
    long_term_memory: bool  # Persistent memory across sessions
    context_awareness: bool  # Understanding of conversation context
    learning_enabled: bool  # Learning from interactions
```

## Agent Capabilities

### Conversation Participation
```python
# Agent joins conversation and responds intelligently
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.join_conversation,
    agent_id="agent_123",
    conversation_id="conv_456",
    auto_respond=True,
    response_trigger="mentioned"
)
```

### Context Management
```python
# Agent maintains conversation context
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.update_context,
    agent_id="agent_123",
    context_data={
        "project": "AGInfrastructure",
        "topic": "extension development",
        "user_preferences": {"code_style": "pythonic"}
    }
)
```

### Task Execution
```python
# Agent executes complex tasks
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.execute_task,
    agent_id="agent_123",
    task_type="code_review",
    task_data={
        "code": "def hello(): return 'world'",
        "criteria": ["style", "performance", "security"]
    }
)
```

## Project Organization

### Project-based Agent Management
```python
# Organize agents by project
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.create_project,
    name="Web Development",
    description="Frontend and backend development agents",
    agents=["frontend_agent", "backend_agent", "qa_agent"]
)
```

### Agent Collaboration
```python
# Agents collaborate on tasks
result = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.create_collaboration,
    project_id="proj_123",
    task="Create user authentication system",
    participating_agents=["backend_agent", "security_agent"]
)
```

## Activity Tracking

### Activity Types
- **Message Activities**: Agent responses in conversations
- **Task Activities**: Task execution and completion
- **Learning Activities**: Knowledge acquisition and updates
- **Collaboration Activities**: Multi-agent interactions

### Activity Monitoring
```python
# Monitor agent activities
activities = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.get_agent_activities,
    agent_id="agent_123",
    time_range="last_7_days",
    activity_types=["conversation", "task_execution"]
)
```

### Performance Analytics
```python
# Analyze agent performance
analytics = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.analyze_performance,
    agent_id="agent_123",
    metrics=["response_quality", "task_success_rate", "user_satisfaction"]
)
```

## Database Schema

### Core Tables
- **Agents**: Agent definitions and configurations
- **AgentPersonalities**: Agent personality settings
- **AgentProviderInstances**: AI provider bindings for agents
- **AgentActivities**: Activity tracking and logging
- **AgentProjects**: Project-based agent organization
- **AgentCollaborations**: Multi-agent collaboration records

### Agent Models
```python
class AgentModel:
    """Core agent entity."""
    id: str
    name: str
    description: str
    personality_config: Dict[str, Any]
    ai_provider_id: str
    model_id: str
    system_prompt: str
    capabilities: List[str]
    created_at: datetime
    
class AgentActivityModel:
    """Agent activity tracking."""
    id: str
    agent_id: str
    activity_type: str
    conversation_id: Optional[str]
    input_data: Dict[str, Any]
    output_data: Dict[str, Any]
    success: bool
    duration_ms: int
    created_at: datetime
```

## Agent Abilities

### Built-in Abilities
- **conversation_participation**: Join and respond in conversations
- **context_management**: Maintain conversation and task context
- **task_execution**: Execute specialized tasks
- **knowledge_retrieval**: Access and provide information
- **learning_adaptation**: Learn from interactions
- **code_generation**: Generate and review code
- **content_creation**: Create various types of content

### Custom Ability Development
```python
class CustomAgentAbility(AbstractAgentAbility):
    """Custom agent ability implementation."""
    
    name = "data_analysis"
    description = "Analyze datasets and provide insights"
    
    @classmethod
    async def execute(cls, agent_id: str, input_data: Dict) -> Dict:
        # Custom ability implementation
        pass
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_AGENTS_MAX_MEMORY` | `50` | Maximum conversation memory size |
| `AI_AGENTS_DEFAULT_MODEL` | `gpt-3.5-turbo` | Default AI model for agents |
| `AI_AGENTS_AUTO_RESPOND` | `false` | Enable auto-response by default |
| `AI_AGENTS_ACTIVITY_LOGGING` | `true` | Enable activity logging |
| `AI_AGENTS_COLLABORATION_ENABLED` | `true` | Enable multi-agent collaboration |
| `AI_AGENTS_LEARNING_ENABLED` | `false` | Enable agent learning |
| `AI_AGENTS_MAX_CONCURRENT_TASKS` | `10` | Maximum concurrent tasks per agent |

## Usage Examples

### Creating Specialized Agents
```python
# Create a code review agent
code_agent = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.create_agent,
    name="Code Reviewer",
    personality={
        "communication_style": "technical",
        "knowledge_domain": ["programming", "best_practices"],
        "response_length": "detailed"
    },
    system_prompt="You are an expert code reviewer focusing on Python best practices",
    abilities=["code_review", "security_analysis", "performance_optimization"]
)
```

### Agent Conversation Integration
```python
# Agent automatically responds to code questions
await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.configure_auto_response,
    agent_id=code_agent.agent_id,
    conversation_filters={
        "keywords": ["code", "python", "review"],
        "user_mentions": True,
        "question_detection": True
    },
    response_delay=2  # seconds
)
```

### Multi-Agent Collaboration
```python
# Create development team of agents
team = await EXT_AI_Agents.root.rotate(
    EXT_AI_Agents.create_agent_team,
    name="Development Team",
    agents=[
        {"role": "architect", "agent_id": "architect_agent"},
        {"role": "developer", "agent_id": "dev_agent"},
        {"role": "tester", "agent_id": "qa_agent"}
    ],
    collaboration_rules={
        "workflow": "architect -> developer -> tester",
        "handoff_criteria": "task_completion",
        "review_required": True
    }
)
```

## Testing

### AI Agents Extension Testing
```python
class TestAIAgentsExtension(AbstractEXTTest):
    extension_class = EXT_AI_Agents
    
    def test_agent_creation(self, extension_server, extension_db):
        """Test agent creation and configuration."""
        result = EXT_AI_Agents.root.rotate(
            EXT_AI_Agents.create_agent,
            name="Test Agent",
            ai_provider="openai",
            model="gpt-3.5-turbo"
        )
        
        assert result.success
        assert result.agent.name == "Test Agent"
    
    def test_agent_conversation(self, extension_server, extension_db):
        """Test agent conversation participation."""
        # Create agent and conversation
        agent = self.create_test_agent()
        conversation = self.create_test_conversation()
        
        # Agent joins and responds
        result = EXT_AI_Agents.root.rotate(
            EXT_AI_Agents.join_conversation,
            agent_id=agent.id,
            conversation_id=conversation.id
        )
        
        assert result.success
```

## Performance Considerations

1. **AI Provider Integration**: Efficient use of AI framework provider rotation
2. **Context Management**: Optimize agent memory and context storage
3. **Activity Logging**: Balance detailed logging with performance
4. **Concurrent Operations**: Handle multiple agent activities simultaneously
5. **Resource Usage**: Monitor AI token usage and costs per agent
6. **Response Time**: Optimize agent response times for real-time conversations

## Best Practices

1. **Agent Design**: Create agents with clear purposes and capabilities
2. **Personality Configuration**: Design consistent and helpful agent personalities
3. **Context Management**: Maintain relevant context without information overload
4. **Activity Monitoring**: Track agent performance and user satisfaction
5. **Resource Management**: Monitor and optimize AI usage costs
6. **Collaboration Design**: Plan multi-agent workflows and handoffs
7. **Learning Strategy**: Implement controlled learning with user feedback
8. **Testing**: Thoroughly test agent behavior in various scenarios