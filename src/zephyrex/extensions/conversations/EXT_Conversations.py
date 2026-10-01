"""
Conversations extension for AGInfrastructure.
Implements conversation management capabilities for user-to-user communication.
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

from fastapi import Depends, HTTPException, Path
from pydantic import BaseModel

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.fastapi import AuthType, static_route
from zephyrex.logic.BLL_Auth import UserManager


class AddParticipantRequest(BaseModel):
    user_id: str
    role: str = "participant"


class CreateDirectMessageRequest(BaseModel):
    other_user_id: str
    initial_message: Optional[str] = None


class EXT_Conversations(AbstractStaticExtension):
    """
    Conversations extension for AGInfrastructure.

    Provides comprehensive conversation management capabilities for user-to-user communication
    including direct messages, group chats, message history, artifact sharing, and conversation
    moderation. This extension focuses on enabling rich communication features between users
    with support for group chat management, participant roles, and conversation organization.

    The extension focuses on:
    - User-to-user conversations and group chats
    - Conversation participant management with roles
    - Message history and threading
    - File and artifact sharing
    - Conversation moderation and permissions
    - Message editing and feedback
    - Read status tracking and notifications
    - Conversation organization and search

    This is an internal database extension that uses BLL managers directly
    rather than external service providers.
    """

    # Extension metadata
    name: ClassVar[str] = "conversations"
    friendly_name: ClassVar[str] = "Conversation Management"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Conversation management extension providing comprehensive conversation capabilities "
        "for direct messages, group chats, message threading, and collaborative communication"
    )

    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "CONVERSATIONS_MAX_PARTICIPANTS": "50",
        "CONVERSATIONS_MESSAGE_LIMIT": "5000",
        "CONVERSATIONS_THREAD_DEPTH": "10",
        "CONVERSATIONS_ENABLE_ENCRYPTION": "false",
    }

    # Unified dependencies for this extension
    dependencies: ClassVar[Dependencies] = Dependencies([])

    # Static abilities provided by this extension
    _abilities: ClassVar[Set[str]] = {
        "manage_conversations",
        "manage_participants",
        "moderate_conversations",
        "track_conversation_analytics",
    }

    # Conversations doesn't use external providers - override providers property
    _providers: ClassVar[List] = []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return the abilities this extension provides."""
        return cls._abilities.copy()

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in cls._abilities

    @classmethod
    def on_initialize(cls) -> bool:
        """Initialize the Conversations extension."""
        logger.debug("Initializing Conversations Extension...")

        try:
            logger.debug("Conversations extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Conversations extension: {str(e)}")
            return False

    @classmethod
    def on_start(cls) -> bool:
        """Start the Conversations extension."""
        try:
            logger.debug("Conversations extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start Conversations extension: {e}")
            return False

    @classmethod
    def on_stop(cls) -> bool:
        """Stop the Conversations extension."""
        try:
            logger.debug("Conversations extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping Conversations extension: {e}")
            return False

    @ability
    @classmethod
    def manage_conversations(cls, **kwargs) -> Dict[str, Any]:
        """
        Meta ability: Manage conversations and chat groups.

        Returns:
            Dict containing conversation management information
        """
        try:
            # In real implementation, would query ConversationModel records
            return {
                "success": True,
                "total_conversations": 0,
                "active_conversations": 0,
                "message": "Conversation management capability",
            }
        except Exception as e:
            logger.error(f"Error managing conversations: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def manage_participants(
        cls,
        conversation_id: str,
        participants: List[str],
        action: str = "add",
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Meta ability: Manage conversation participants and their roles.

        Args:
            conversation_id: ID of the conversation
            participants: List of user IDs
            action: Action to perform ("add", "remove", "update_role")

        Returns:
            Participant management result
        """
        try:
            return {
                "success": True,
                "conversation_id": conversation_id,
                "participants": participants,
                "action": action,
                "message": f"Successfully {action} participants in conversation {conversation_id}",
            }
        except Exception as e:
            logger.error(f"Error managing participants: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def moderate_conversations(
        cls, conversation_id: str = None, moderation_action: str = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Moderate conversations and messages.

        Args:
            conversation_id: Optional conversation ID to moderate
            moderation_action: Type of moderation action

        Returns:
            Moderation result
        """
        try:
            return {
                "success": True,
                "conversation_id": conversation_id,
                "moderation_action": moderation_action,
                "message": "Conversation moderation capability",
            }
        except Exception as e:
            logger.error(f"Error moderating conversations: {e}")
            return {"success": False, "error": str(e)}

    @ability
    @classmethod
    def track_conversation_analytics(
        cls, conversation_id: str = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Meta ability: Track analytics for conversations.

        Args:
            conversation_id: Optional conversation ID for specific analytics

        Returns:
            Analytics information
        """
        try:
            # In real implementation, would query MessageModel and analytics data
            return {
                "success": True,
                "total_messages": 0,
                "active_users": 0,
                "message_frequency": {},
                "message": f"Analytics for {'conversation ' + conversation_id if conversation_id else 'all conversations'}",
            }
        except Exception as e:
            logger.error(f"Error tracking conversation analytics: {e}")
            return {"success": False, "error": str(e)}

    # Static routes for conversation operations
    @classmethod
    @static_route(
        "/v1/conversations/{conversation_id}/participants",
        method="POST",
        auth_type=AuthType.JWT,
        summary="Add participant to conversation",
        description="Add a user as a participant to an existing conversation",
        tags=["Conversations"],
    )
    def add_participant(
        cls,
        conversation_id: str = Path(..., description="Conversation ID"),
        request: AddParticipantRequest = ...,
        user=Depends(UserManager.auth),
        model_registry=Depends(lambda: None),  # Will be injected
    ) -> Dict[str, Any]:
        """Add a participant to a conversation."""
        from zephyrex.extensions.conversations.BLL_Conversations import ConversationManager

        # Create manager instance
        manager = ConversationManager(
            requester_id=user.id,
            model_registry=model_registry,
        )

        try:

            participant = manager.add_participant(
                conversation_id=conversation_id,
                user_id=request.user_id,
            )
            return {"conversation_user": participant}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding participant: {e}")
            raise HTTPException(status_code=500, detail="Failed to add participant")

    @classmethod
    @static_route(
        "/v1/conversations/{conversation_id}/participants/{user_id}",
        method="DELETE",
        auth_type=AuthType.JWT,
        summary="Remove participant from conversation",
        description="Remove a user from a conversation",
        tags=["Conversations"],
    )
    def remove_participant(
        cls,
        conversation_id: str = Path(..., description="Conversation ID"),
        user_id: str = Path(..., description="User ID to remove"),
        user=Depends(UserManager.auth),
        model_registry=Depends(lambda: None),  # Will be injected
    ) -> Dict[str, Any]:
        """Remove a participant from a conversation."""
        from zephyrex.extensions.conversations.BLL_Conversations import ConversationManager

        # Create manager instance
        manager = ConversationManager(
            requester_id=user.id,
            model_registry=model_registry,
        )

        try:
            participant = manager.remove_participant(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            return {"conversation_user": participant}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing participant: {e}")
            raise HTTPException(status_code=500, detail="Failed to remove participant")

    @classmethod
    @static_route(
        "/v1/conversations/direct-message",
        method="POST",
        auth_type=AuthType.JWT,
        summary="Create direct message conversation",
        description="Create a direct message conversation between two users",
        tags=["Conversations"],
    )
    def create_direct_message(
        cls,
        request: CreateDirectMessageRequest = ...,
        user=Depends(UserManager.auth),
        model_registry=Depends(lambda: None),  # Will be injected
    ) -> Dict[str, Any]:
        """Create a direct message conversation."""
        from zephyrex.extensions.conversations.BLL_Conversations import (
            ConversationManager,
        )

        # Create manager instance
        manager = ConversationManager(
            requester_id=user.id,
            model_registry=model_registry,
        )

        try:
            result = manager.create_direct_message(
                other_user_id=request.other_user_id,
                initial_message=request.initial_message,
            )
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating direct message: {e}")
            raise HTTPException(
                status_code=500, detail="Failed to create direct message"
            )

    @classmethod
    @static_route(
        "/v1/conversations/{conversation_id}/messages/{message_id}/thread",
        method="GET",
        auth_type=AuthType.JWT,
        summary="Get message thread",
        description="Get a thread of messages starting from a parent message",
        tags=["Conversations"],
    )
    def get_message_thread(
        cls,
        conversation_id: str = Path(..., description="Conversation ID"),
        message_id: str = Path(..., description="Message ID"),
        depth: int = 0,
        user=Depends(UserManager.auth),
        model_registry=Depends(lambda: None),  # Will be injected
    ) -> Dict[str, Any]:
        """Get a message thread."""
        from zephyrex.extensions.conversations.BLL_Conversations import MessageManager

        # Create manager instance
        manager = MessageManager(
            requester_id=user.id,
            model_registry=model_registry,
        )

        try:
            thread = manager.get_thread(message_id=message_id, depth=depth)
            return {"messages": thread}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting message thread: {e}")
            raise HTTPException(status_code=500, detail="Failed to get message thread")
