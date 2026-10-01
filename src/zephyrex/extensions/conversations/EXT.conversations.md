# Conversations Extension

Conversation management extension for AGInfrastructure providing comprehensive user-to-user communication capabilities through database models and business logic managers.

## Overview

The `conversations` extension provides a complete conversation management framework for direct messages, group chats, and collaborative communication. This is a **database extension** that uses BLL managers directly rather than external service providers.

## Architecture

### Extension Class Structure
```python
class EXT_Conversations(AbstractStaticExtension):
    """
    Conversations extension for AGInfrastructure.
    
    Provides comprehensive conversation management capabilities for user-to-user communication
    including direct messages, group chats, message history, artifact sharing, and conversation
    moderation. This extension focuses on enabling rich communication features between users
    with support for group chat management, participant roles, and conversation organization.
    """
    
    name: ClassVar[str] = "conversations"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Conversation management extension providing comprehensive conversation capabilities "
        "for direct messages, group chats, message threading, and collaborative communication"
    )
    
    # Meta abilities provided by this extension
    _abilities: ClassVar[Set[str]] = {
        "manage_conversations",
        "manage_participants", 
        "moderate_conversations",
        "track_conversation_analytics",
    }
```

### Database Models

#### Core Models
The extension provides several database models for conversation management:

- **ConversationModel**: Core conversation entity with support for private, public, and invite-only visibility
- **ConversationUserModel**: Many-to-many relationship between users and conversations with roles
- **MessageModel**: Individual messages within conversations with threading support
- **FeedbackModel**: User feedback on messages for quality assessment
- **ArtifactModel**: File and content attachments associated with conversations

#### Model Relationships
```python
class ConversationModel(BaseMixinModel, DatabaseMixin):
    """Main conversation entity."""
    name: Optional[str]
    description: Optional[str] 
    is_group_chat: bool = False
    visibility: ConversationVisibility = ConversationVisibility.PRIVATE
    max_participants: Optional[int] = None

class MessageModel(BaseMixinModel, DatabaseMixin):
    """Messages within conversations."""
    conversation_id: str
    user_id: str
    content: str
    parent_id: Optional[str]  # For threading
    edited_at: Optional[datetime]
    is_deleted: bool = False

class ConversationUserModel(BaseMixinModel, DatabaseMixin):
    """User participation in conversations."""
    conversation_id: str
    user_id: str
    is_active: bool = True
    last_read_at: Optional[datetime]
```

## Business Logic Managers

### ConversationManager
Primary manager for conversation operations:

```python
from extensions.conversations.BLL_Conversations import ConversationManager

# Initialize manager
manager = ConversationManager(
    requester_id=user_id,
    model_registry=model_registry
)

# Create a conversation
conversation = manager.create(
    name="Project Discussion",
    description="Team collaboration chat",
    is_group_chat=True,
)

# Add participants
participant = manager.add_participant(
    conversation_id=conversation.id,
    user_id=other_user_id,
)
```

### MessageManager
Manager for message operations within conversations:

```python
# Access through ConversationManager
messages = manager.messages

# Create a message
message = messages.create(
    conversation_id=conversation.id,
    content="Hello team!",
    user_id=user_id
)

# Get message thread
thread = messages.get_thread(
    message_id=message.id,
    depth=5  # Maximum thread depth
)
```

### User Model Extensions
The extension adds conversation-specific fields to the User model:

```python
@extension_model(UserModel)
class Conversations_UserModel(BaseModel):
    """User model extension for conversations."""
    
    conversation_preferences: Optional[Dict] = None
    last_conversation_activity_at: Optional[datetime] = None
    conversation_notification_settings: Optional[Dict] = None
```

## API Endpoints

The extension provides static route endpoints for common operations:

### Participant Management
- `POST /v1/conversations/{conversation_id}/participants` - Add participant to conversation
- `DELETE /v1/conversations/{conversation_id}/participants/{user_id}` - Remove participant from conversation

### Direct Messaging
- `POST /v1/conversations/direct-message` - Create direct message conversation

### Message Threading
- `GET /v1/conversations/{conversation_id}/messages/{message_id}/thread` - Get message thread

## UserManager Extensions

The extension injects additional methods into UserManager for conversation operations:

### Conversation Management
```python
# Get user's conversations
conversations = user_manager.get_user_conversations(
    user_id=user_id,
    active_only=True,
    limit=20
)

# Create direct message
result = user_manager.create_direct_message(
    user_id=current_user_id,
    other_user_id=target_user_id,
    initial_message="Hi there!"
)

# Create group conversation
group = user_manager.create_group_conversation(
    creator_id=user_id,
    name="Team Chat",
    participant_ids=["user_2", "user_3", "user_4"],
    description="Weekly team discussion"
)
```

### User Preferences
```python
# Get conversation preferences
prefs = user_manager.get_conversation_preferences(user_id=user_id)

# Set conversation preferences
user_manager.set_conversation_preferences(
    user_id=user_id,
    preferences={
        "theme": "dark",
        "notifications_enabled": True,
        "sound_enabled": False
    }
)

# Get conversation statistics
stats = user_manager.get_conversation_stats(user_id=user_id)
# Returns: total_conversations, direct_messages, group_chats, unread_count
```

## Conversation Features

### Message Threading
Messages support threading through parent-child relationships:
- Reply to messages create threaded discussions
- Get full conversation threads with configurable depth
- Track conversation context and flow

### File Sharing
Artifacts can be attached to conversations and messages:
- File uploads with metadata tracking
- Encrypted file storage support
- MIME type validation and processing

## Hook Integration

The extension includes hooks for activity tracking:

### Message Activity Tracking
```python
@hook_bll(MessageManager.create, timing="after")
def track_message_activity(context: HookContext):
    """Update user's last conversation activity when messages are created."""
    # Updates last_conversation_activity_at timestamp
```

### Read Status Tracking
```python
@hook_bll(ConversationUserManager.update, timing="after") 
def track_conversation_read_activity(context: HookContext):
    """Track user activity when conversation read status is updated."""
    # Updates activity when last_read_at is modified
```

## Usage Examples

### Creating Conversations
```python
# Direct message between two users
dm = user_manager.create_direct_message(
    user_id="alice_123",
    other_user_id="bob_456", 
    initial_message="Hey Bob, can we discuss the project?"
)

# Group conversation with multiple participants
group = user_manager.create_group_conversation(
    creator_id="alice_123",
    name="Project Alpha Team",
    participant_ids=["bob_456", "charlie_789", "diana_012"],
    description="Coordination for Project Alpha deliverables",
    max_participants=10
)
```

### Message Management
```python
# Send message to conversation
message = conversation_manager.messages.create(
    conversation_id=conversation.id,
    content="Here's the latest update on our progress...",
    user_id=user_id
)

# Reply to a message (threading)
reply = conversation_manager.messages.create(
    conversation_id=conversation.id,
    content="Thanks for the update!",
    parent_id=message.id,
    user_id=other_user_id
)

# Edit a message
edited = conversation_manager.messages.update(
    id=message.id,
    content="Here's the UPDATED progress report..."
)
```

### Participant Management
```python
# Add moderator to group chat
conversation_manager.add_participant(
    conversation_id=group_conversation.id,
    user_id="moderator_123",
)

# Remove inactive participant
conversation_manager.remove_participant(
    conversation_id=group_conversation.id,
    user_id="inactive_user_456"
)

# Get all active participants
participants = conversation_manager.get_participants(
    conversation_id=group_conversation.id
)
```

## Testing

### Database Extension Testing
```python
class TestConversationsExtension(AbstractEXTTest):
    extension_class = EXT_Conversations
    
    def test_conversation_creation(self, extension_server, extension_db):
        """Test conversation creation and participant management."""
        # Test runs in isolated test.conversations.database.db
        # All conversation models available
        pass
        
    def test_message_threading(self, extension_server, extension_db):
        """Test message threading functionality."""
        # Test thread creation and retrieval
        pass
```

### Manager Testing
```python
class TestConversationManager(AbstractBLLTest):
    def test_add_participant(self, db_manager):
        """Test adding participants to conversations."""
        manager = ConversationManager(
            requester_id="test_user",
            model_registry=model_registry
        )
        # Test participant addition logic
```

## Environment Variables

| Variable                          | Default | Description                              |
| --------------------------------- | ------- | ---------------------------------------- |
| `CONVERSATIONS_MAX_PARTICIPANTS`  | `50`    | Maximum participants per group chat      |
| `CONVERSATIONS_MESSAGE_LIMIT`     | `5000`  | Maximum message length in characters     |
| `CONVERSATIONS_THREAD_DEPTH`      | `10`    | Maximum thread depth for message replies |
| `CONVERSATIONS_ENABLE_ENCRYPTION` | `false` | Enable message content encryption        |

## Performance Considerations

1. **Message Indexing**: Efficient database indexing on conversation_id, user_id, and created_at
2. **Pagination**: Built-in pagination for message history and conversation lists
3. **Caching**: Manager-level caching for frequently accessed conversations
4. **Thread Optimization**: Optimized queries for message thread retrieval
5. **Activity Tracking**: Efficient updates for user activity timestamps

## Security Features

1. **Participant Validation**: Ensure users can only access conversations they participate in
2. **Role-based Permissions**: Different capabilities based on user role in conversation
3. **Message Encryption**: Optional encryption for sensitive conversations
4. **Audit Logging**: Comprehensive logging of conversation activities
5. **Content Moderation**: Support for message content filtering and reporting

## Best Practices

1. **Manager Usage**: Always use managers through the model registry pattern
2. **Error Handling**: Implement proper error handling for conversation operations
3. **Activity Tracking**: Use hooks for automatic activity timestamp updates
4. **Thread Management**: Be mindful of thread depth to avoid infinite recursion
5. **Permission Checks**: Validate user permissions before conversation operations
6. **Performance**: Use pagination for large conversation histories
7. **User Experience**: Provide real-time updates for active conversations

## Migration Support

Database migrations are provided for:
- Initial schema creation (`51278cce1565_initial_schema.py`)
- Model relationships and foreign keys
- Index creation for performance optimization

Migrations are automatically applied when the extension is loaded.