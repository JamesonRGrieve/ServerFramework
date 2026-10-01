from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException
from pydantic import Field, model_validator

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel


class ConversationModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.ID.Optional,
    metaclass=ModelMeta,
):
    description: Optional[str] = Field(
        None, description="Description of the conversation"
    )
    is_group_chat: bool = Field(
        False, description="Whether this conversation is a group chat"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Conversations represent structured discussions between users"
    )

    class Create(
        BaseModel,
        NameMixinModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        description: Optional[str] = Field(
            None, description="Description of the conversation"
        )
        is_group_chat: bool = Field(
            False, description="Whether this conversation is a group chat"
        )

    class Update(BaseModel, NameMixinModel.Optional):
        description: Optional[str] = Field(
            None, description="Description of the conversation"
        )
        is_group_chat: Optional[bool] = Field(
            None, description="Whether this conversation is a group chat"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        description: Optional[StringSearchModel] = None
        is_group_chat: Optional[bool] = None


class ConversationUserModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    ConversationModel.Reference,
    metaclass=ModelMeta,
):
    # Database metadata
    table_comment: ClassVar[str] = (
        "Associative entity managing the many-to-many relationship between users and "
        "conversations. Represents a user's participation in a conversation."
    )

    class Create(BaseModel, ConversationModel.Reference.ID, UserModel.Reference.ID):
        pass

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        ConversationModel.Reference.ID.Search,
    ):
        pass


class ConversationManager(AbstractBLLManager, RouterMixin):
    _model = ConversationModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/conversation"
    tags: ClassVar[Optional[List[str]]] = ["Conversations"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ) -> None:
        """Initialize ConversationManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            model_registry: Model registry for dynamic model handling
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._messages = None
        self._artifacts = None
        self._conversation_users = None

    @property
    def messages(self) -> "MessageManager":
        """Get the message manager for this conversation manager."""
        if self._messages is None:
            self._messages = MessageManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._messages

    @property
    def artifacts(self) -> "ArtifactManager":
        """Get the artifact manager for this conversation manager."""
        if self._artifacts is None:
            self._artifacts = ArtifactManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._artifacts

    @property
    def conversation_users(self) -> "ConversationUserManager":
        """Get the conversation user manager for this conversation manager."""
        if self._conversation_users is None:
            self._conversation_users = ConversationUserManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._conversation_users

    def create_validation(self, entity):
        """Validate conversation creation."""
        if entity.user_id:
            try:
                from zephyrex.logic.BLL_Auth import UserManager

                UserManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.user_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="User not found")

    def create(self, **kwargs) -> ConversationModel:
        """Create a new conversation and add the creator as the first participant.

        Agent association with newly-created conversations is handled by the
        ``ai_agents`` extension's own ``@hook_bll(ConversationManager.create,
        timing=HookTiming.AFTER)`` registration (see
        ``BLL_AI_Agents.associate_agent_with_conversation``) — the framework's
        hook registry dispatches it automatically via ``wrap_method_with_hooks``
        whenever that extension is loaded. Conversations must not hard-import a
        sibling extension's BLL to invoke it manually; that duplicates the
        dispatch and, when ``ai_agents`` isn't loaded, re-attempts the entire
        (failing) import on every single call.
        """
        conversation = super().create(**kwargs)

        self.add_participant(
            conversation_id=conversation.id,
            user_id=self.requester.id,
        )

        return conversation

    def add_participant(
        self,
        conversation_id: str,
        user_id: str,
    ) -> ConversationUserModel:
        """Add a participant to a conversation, or return their existing membership."""
        existing_participants = self.conversation_users.list(
            conversation_id=conversation_id, user_id=user_id
        )
        if existing_participants:
            return existing_participants[0]

        return self.conversation_users.create(
            conversation_id=conversation_id,
            user_id=user_id,
        )

    def remove_participant(self, conversation_id: str, user_id: str) -> None:
        """Remove a participant from a conversation."""
        participants = self.conversation_users.list(
            conversation_id=conversation_id, user_id=user_id
        )

        if not participants:
            raise HTTPException(
                status_code=404,
                detail="User is not a participant in this conversation",
            )

        participant = participants[0]
        self.conversation_users.delete(id=participant.id)

    def get_participants(self, conversation_id: str) -> List[ConversationUserModel]:
        """Get all participants in a conversation."""
        return self.conversation_users.list(conversation_id=conversation_id)

    def create_direct_message(
        self, other_user_id: str, initial_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """Find or create a direct (non-group) conversation with ``other_user_id``.

        Optionally posts ``initial_message`` into the resulting conversation.
        """
        requester_id = self.requester.id

        existing_conversations = self.list(
            user_id=requester_id, is_group_chat=False
        )
        conversation = None
        for candidate in existing_conversations:
            candidate_id = (
                candidate["id"] if isinstance(candidate, dict) else candidate.id
            )
            participant_ids = {
                (p["user_id"] if isinstance(p, dict) else p.user_id)
                for p in self.conversation_users.list(conversation_id=candidate_id)
            }
            if participant_ids == {requester_id, other_user_id}:
                conversation = candidate
                break

        if conversation is None:
            conversation = self.create(
                user_id=requester_id,
                name="Direct Message",
                is_group_chat=False,
            )
            self.add_participant(
                conversation_id=conversation.id, user_id=other_user_id
            )

        message = None
        if initial_message:
            message = self.messages.create(
                conversation_id=conversation.id,
                content=initial_message,
            )

        return {"conversation": conversation, "message": message}


class ConversationUserManager(AbstractBLLManager, RouterMixin):
    _model = ConversationUserModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/conversation/user"
    tags: ClassVar[Optional[List[str]]] = ["Conversation Users"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity):
        """Validate conversation user creation."""
        if entity.user_id:
            try:
                from zephyrex.logic.BLL_Auth import UserManager

                UserManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.user_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="User not found")

        if entity.conversation_id:
            try:
                ConversationManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.conversation_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="Conversation not found")


class MessageModel(
    ApplicationModel,
    UpdateMixinModel,
    ParentMixinModel.Optional,
    ConversationModel.Reference,
    metaclass=ModelMeta,
):
    content: str = Field(..., description="Content of the message")
    edited_at: Optional[datetime] = Field(
        None, description="When message was last edited"
    )
    is_deleted: bool = Field(False, description="Whether message is soft deleted")
    user_id: Optional[str] = Field(
        None, description="ID of the user sending the message"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Messages represent individual communications within conversations, "
        "supporting threading via parent_id and edit history tracking."
    )

    class Create(BaseModel, ConversationModel.Reference.ID, ParentMixinModel.Optional):
        content: str = Field(..., description="Content of the message")
        user_id: Optional[str] = Field(
            None, description="ID of the user sending the message"
        )
        file: Optional[str] = Field(None, description="Audio file in base64 format")

    class Update(BaseModel):
        content: Optional[str] = Field(None, description="New content for the message")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ParentMixinModel.Search,
        ConversationModel.Reference.ID.Search,
    ):
        content: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        is_deleted: Optional[bool] = None


class MessageManager(AbstractBLLManager, RouterMixin):
    _model = MessageModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/message"
    tags: ClassVar[Optional[List[str]]] = ["Messages"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ) -> None:
        """Initialize MessageManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            model_registry: Model registry for dynamic model handling
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._feedbacks = None

    @property
    def feedbacks(self) -> "FeedbackManager":
        """Get the feedback manager for this message manager."""
        if self._feedbacks is None:
            self._feedbacks = FeedbackManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._feedbacks

    def create_validation(self, entity):
        """Validate message creation."""
        if entity.conversation_id:
            try:
                ConversationManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.conversation_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="Conversation not found")

        if entity.parent_id:
            try:
                MessageManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.parent_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="Parent message not found")

    def create(self, **kwargs) -> MessageModel:
        """Create a new message in a conversation."""
        if "user_id" not in kwargs:
            kwargs["user_id"] = self.requester.id

        if "file" in kwargs:
            try:
                from zephyrex.extensions.ai.BLL_AI import transcribe_audio_to_text

                resp = transcribe_audio_to_text(self, **kwargs)
                if resp:
                    if resp == "Transcription failed":
                        raise HTTPException(status_code=500, detail=resp)
                    kwargs["content"] = resp
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error transcribing audio to text: {e}")
            finally:
                kwargs.pop("file", None)

        # Agent auto-response to newly-created messages is handled by the
        # ``ai_agents`` extension's own ``@hook_bll(MessageManager.create,
        # timing="after")`` registration (see
        # ``BLL_AI_Agents.auto_respond_agents_on_message``) — the framework's
        # hook registry dispatches it automatically via ``wrap_method_with_hooks``
        # whenever that extension is loaded. See ``ConversationManager.create``
        # above for why a manual, hard-imported invocation here is wrong.
        return super().create(**kwargs)

    def update(self, id: str, **kwargs) -> MessageModel:
        """Update a message, tracking edit history."""
        if "content" in kwargs:
            kwargs["edited_at"] = datetime.now(timezone.utc)

        return super().update(id, **kwargs)

    @staticmethod
    def edit(
        manager: "MessageManager", id: str, content: str, fork: bool = False
    ) -> MessageModel:
        """Edit a message, optionally creating a fork (reply) instead of in-place edit."""
        if fork:
            original_message = manager.get(id=id)
            return manager.create(
                conversation_id=original_message.conversation_id,
                content=content,
                parent_id=id,
            )
        return manager.update(id, content=content)

    def get_thread(self, message_id: str, depth: int = 0) -> List[MessageModel]:
        """Get a thread of messages starting from a parent message."""
        thread = []

        parent = self.get(id=message_id)
        thread.append(parent)

        if depth != 0:  # 0 means no limit, negative values also mean no limit
            replies = self.list(parent_id=message_id)
            for reply in replies:
                reply_id = reply["id"] if isinstance(reply, dict) else reply.id
                sub_thread = self.get_thread(
                    reply_id, depth - 1 if depth > 0 else depth
                )
                thread.extend(sub_thread)

        return thread


class FeedbackModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.ID.Optional,
    MessageModel.Reference.ID,
    metaclass=ModelMeta,
):
    content: str = Field(..., description="Feedback content")
    positive: Optional[bool] = Field(
        None,
        description="Whether this is positive (True), negative (False), or neutral (None) feedback",
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Feedback on messages from users, supporting positive/negative/neutral sentiment "
        "for message quality assessment and AI training."
    )

    class Create(BaseModel, MessageModel.Reference.ID, UserModel.Reference.ID.Optional):
        content: str = Field(..., description="Feedback content")
        positive: Optional[bool] = Field(
            None, description="Whether this is positive feedback"
        )

    class Update(BaseModel):
        content: Optional[str] = Field(None, description="Feedback content")
        positive: Optional[bool] = Field(
            None, description="Whether this is positive feedback"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        MessageModel.Reference.ID.Search,
    ):
        content: Optional[StringSearchModel] = None
        positive: Optional[bool] = None


class FeedbackManager(AbstractBLLManager, RouterMixin):
    _model = FeedbackModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/feedback"
    tags: ClassVar[Optional[List[str]]] = ["Feedback"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity):
        """Validate feedback creation."""
        if entity.message_id:
            try:
                MessageManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.message_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="Message not found")

    def create(self, **kwargs) -> FeedbackModel:
        """Create a new feedback entry."""
        if "user_id" not in kwargs:
            kwargs["user_id"] = self.requester.id

        return super().create(**kwargs)


class ArtifactModel(
    ApplicationModel,
    UpdateMixinModel,
    ParentMixinModel.Optional,
    NameMixinModel.Optional,
    UserModel.Reference.Optional,
    ConversationModel.Reference.Optional,
    MessageModel.Reference.Optional,
    metaclass=ModelMeta,
):
    relative_path: str = Field(..., description="Relative path to the artifact")
    hosted_path: str = Field(..., description="Hosted path to the artifact")
    content: Optional[str] = Field(None, description="Embedded content of the artifact")
    encrypted: bool = Field(False, description="Whether the artifact is encrypted")
    file_size: Optional[int] = Field(None, description="File size in bytes")
    mime_type: Optional[str] = Field(None, description="MIME type of the file")

    # Database metadata
    table_comment: ClassVar[str] = (
        "Artifacts represent files and content attachments associated with conversations "
        "and messages, supporting encryption and various content types."
    )

    class Create(
        BaseModel,
        NameMixinModel,
        ParentMixinModel.Optional,
        UserModel.Reference.ID.Optional,
    ):
        relative_path: str = Field(..., description="Relative path to the artifact")
        hosted_path: str = Field(..., description="Hosted path to the artifact")
        content: Optional[str] = Field(
            None, description="Embedded content of the artifact"
        )
        conversation_id: Optional[str] = Field(
            None, description="ID of the conversation this artifact belongs to"
        )
        message_id: Optional[str] = Field(None, description="ID of the source message")
        encrypted: bool = Field(False, description="Whether the artifact is encrypted")
        file_size: Optional[int] = Field(None, description="File size in bytes")
        mime_type: Optional[str] = Field(None, description="MIME type of the file")

        @model_validator(mode="after")
        def validate_artifact_create(self):
            """Ensure at least one of conversation_id or message_id is provided."""
            if not self.conversation_id and not self.message_id:
                raise ValueError(
                    "Either conversation_id or message_id must be provided"
                )
            return self

    class Update(BaseModel, NameMixinModel.Optional, ParentMixinModel.Optional):
        relative_path: Optional[str] = Field(
            None, description="Relative path to the artifact"
        )
        hosted_path: Optional[str] = Field(
            None, description="Hosted path to the artifact"
        )
        content: Optional[str] = Field(
            None, description="Embedded content of the artifact"
        )
        conversation_id: Optional[str] = Field(
            None, description="ID of the conversation this artifact belongs to"
        )
        message_id: Optional[str] = Field(None, description="ID of the source message")
        encrypted: Optional[bool] = Field(
            None, description="Whether the artifact is encrypted"
        )
        file_size: Optional[int] = Field(None, description="File size in bytes")
        mime_type: Optional[str] = Field(None, description="MIME type of the file")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        ParentMixinModel.Search,
        UserModel.Reference.ID.Search,
        ConversationModel.Reference.ID.Search,
        MessageModel.Reference.ID.Search,
    ):
        relative_path: Optional[StringSearchModel] = None
        hosted_path: Optional[StringSearchModel] = None
        file_size: Optional[int] = None
        mime_type: Optional[StringSearchModel] = None
        encrypted: Optional[bool] = None


class ArtifactManager(AbstractBLLManager, RouterMixin):
    _model = ArtifactModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/artifact"
    tags: ClassVar[Optional[List[str]]] = ["Artifacts"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity):
        """Validate artifact creation."""
        if entity.conversation_id:
            try:
                ConversationManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.conversation_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="Conversation not found")

        if entity.message_id:
            try:
                MessageManager(
                    requester_id=env("SYSTEM_ID"),
                    model_registry=self.model_registry,
                ).get(id=entity.message_id)
            except HTTPException:
                raise HTTPException(status_code=404, detail="Message not found")

        if not entity.conversation_id and not entity.message_id:
            raise HTTPException(
                status_code=400, detail="Artifact must belong to a conversation"
            )

    def create(self, **kwargs) -> ArtifactModel:
        """Create a new artifact."""
        if "user_id" not in kwargs:
            kwargs["user_id"] = self.requester.id

        return super().create(**kwargs)
