# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conversations between users (and their agents): direct messages and
group chats, threaded messages, feedback on messages, and artifacts.

Access follows the conversation. Its owner holds it, and each participant
added is granted view and edit on it (a Permission row), which lets them
read and post. Participants, messages, feedback and artifacts inherit
access from the conversation (``permission_references``).

A message's author is whoever posts it; the author cannot be named by the
caller. Agent messages carry no author and are posted only through
:meth:`MessageManager.create_agent_message`. Only an author edits their
message; an author or the conversation's owner deletes it. Feedback is
likewise its author's. Only the owner deletes a conversation; the owner
removes anyone, and a participant may leave.
"""

from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel as RouteModel
from pydantic import Field, model_validator

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.Preconditions import expect_route_record
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.AbstractLogicManager.ownership import (
    created_records,
    each_created,
    owned_by,
    server_side,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

MAX_THREAD_DEPTH = 10
MAX_THREAD_MESSAGES = 1000


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
    table_comment: ClassVar[str] = (
        "Associative entity managing the many-to-many relationship between users and "
        "conversations. Represents a user's participation in a conversation."
    )
    permission_references: ClassVar[List[str]] = ["conversation"]

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


class ParticipantRequest(RouteModel):
    user_id: str = Field(..., description="The user to add")


class ParticipantRemoved(RouteModel):
    conversation_id: str
    user_id: str


class DirectMessageRequest(RouteModel):
    other_user_id: str = Field(..., description="The other user")
    initial_message: Optional[str] = Field(None, description="A first message")


class DirectMessageResponse(RouteModel):
    conversation: ConversationModel
    message: Optional["MessageModel"] = None


class ConversationManager(AbstractBLLManager, RouterMixin):
    _model = ConversationModel

    prefix: ClassVar[Optional[str]] = "/v1/conversation"
    tags: ClassVar[Optional[List[str]]] = ["Conversations"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    @property
    def messages(self) -> "MessageManager":
        return MessageManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

    @property
    def artifacts(self) -> "ArtifactManager":
        return ArtifactManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

    @property
    def conversation_users(self) -> "ConversationUserManager":
        return ConversationUserManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

    def create_validation(self, entity: Any) -> None:
        if entity.user_id:
            _visible_user(self.model_registry, self.requester.id, entity.user_id)

    def create(self, **kwargs: Any) -> Any:
        """A conversation owned by its creator, who is its first participant.
        Agent association is the ``ai_agents`` extension's create hook."""
        result = super().create(**each_created(kwargs, owned_by(self.requester.id)))
        for conversation in created_records(result):
            self.add_participant(
                conversation_id=conversation.id, user_id=conversation.user_id
            )
        return result

    def delete(self, id: str) -> None:
        """Only the owner deletes a conversation."""
        conversation = self.get(id=id)
        if (
            not server_side(self.requester.id)
            and conversation.user_id != self.requester.id
        ):
            raise HTTPException(
                status_code=403, detail="Only the owner deletes a conversation"
            )
        super().delete(id)

    def add_participant(
        self, conversation_id: str, user_id: str
    ) -> ConversationUserModel:
        """Add ``user_id`` (or return their membership), granting them view
        and edit on the conversation. The requester must be able to edit it
        and to see the user."""
        existing = self.conversation_users.list(
            conversation_id=conversation_id, user_id=user_id
        )
        if existing:
            found: ConversationUserModel = existing[0]
            return found
        added: ConversationUserModel = self.conversation_users.create(
            conversation_id=conversation_id, user_id=user_id
        )
        return added

    def remove_participant(self, conversation_id: str, user_id: str) -> None:
        """The owner removes anyone; a participant may leave."""
        conversation = self.get(id=conversation_id)
        if not server_side(self.requester.id) and self.requester.id not in (
            conversation.user_id,
            user_id,
        ):
            raise HTTPException(
                status_code=403,
                detail="Only the owner removes others from a conversation",
            )
        if user_id == conversation.user_id:
            raise HTTPException(
                status_code=400, detail="The owner cannot leave their conversation"
            )
        memberships = self.conversation_users.list(
            conversation_id=conversation_id, user_id=user_id
        )
        if not memberships:
            raise HTTPException(
                status_code=404,
                detail="User is not a participant in this conversation",
            )
        root = ConversationUserManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        for membership in memberships:
            # The route names the membership by two ids; it is held to the
            # membership row's version, answered as the requester sees it.
            with expect_route_record(self.conversation_users, membership.id):
                root.delete_row(membership.id)
        _revoke(self.model_registry, conversation_id, user_id)

    def get_participants(self, conversation_id: str) -> List[ConversationUserModel]:
        return self.conversation_users.list(conversation_id=conversation_id)

    def create_direct_message(
        self, other_user_id: str, initial_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """The direct conversation between the requester and
        ``other_user_id``, made if there is none, with ``initial_message``
        posted into it. The requester must see ``other_user_id``; nothing is
        written for a user they cannot."""
        requester_id = self.requester.id
        if other_user_id == requester_id:
            raise HTTPException(
                status_code=400, detail="A direct message needs another user"
            )
        _visible_user(self.model_registry, requester_id, other_user_id)
        conversation = None
        for candidate in self.list(is_group_chat=False):
            members = {
                member.user_id
                for member in self.conversation_users.list(conversation_id=candidate.id)
            }
            if members == {requester_id, other_user_id}:
                conversation = candidate
                break
        if conversation is None:
            conversation = self.create(name="Direct Message", is_group_chat=False)
            self.add_participant(conversation_id=conversation.id, user_id=other_user_id)
        message = None
        if initial_message:
            message = self.messages.create(
                conversation_id=conversation.id, content=initial_message
            )
        return {"conversation": conversation, "message": message}

    @custom_route(
        method="POST",
        path="/{conversation_id}/participants",
        input_model=ParticipantRequest,
        output_model=ConversationUserModel,
        authentication_type="jwt",
        openapi_tags=("Conversations",),
        summary="Add a participant to a conversation",
        expose_in=(ExposeIn.REST,),
    )
    def add_participant_route(
        self, conversation_id: str, body: ParticipantRequest
    ) -> ConversationUserModel:
        return self.add_participant(conversation_id, body.user_id)

    @custom_route(
        method="DELETE",
        path="/{conversation_id}/participants/{user_id}",
        authentication_type="jwt",
        openapi_tags=("Conversations",),
        summary="Remove a participant from a conversation, or leave it",
        output_model=ParticipantRemoved,
        expose_in=(ExposeIn.REST,),
    )
    def remove_participant_route(
        self, conversation_id: str, user_id: str
    ) -> ParticipantRemoved:
        self.remove_participant(conversation_id, user_id)
        return ParticipantRemoved(conversation_id=conversation_id, user_id=user_id)

    @custom_route(
        method="POST",
        path="/direct",
        input_model=DirectMessageRequest,
        output_model=DirectMessageResponse,
        authentication_type="jwt",
        openapi_tags=("Conversations",),
        summary="Open (or find) a direct conversation with another user",
        expose_in=(ExposeIn.REST,),
    )
    def direct_message_route(self, body: DirectMessageRequest) -> DirectMessageResponse:
        return DirectMessageResponse(
            **self.create_direct_message(body.other_user_id, body.initial_message)
        )


class ConversationUserManager(AbstractBLLManager, RouterMixin):
    _model = ConversationUserModel

    prefix: ClassVar[Optional[str]] = "/v1/conversation/user"
    tags: ClassVar[Optional[List[str]]] = ["Conversation Users"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity: Any) -> None:
        # The requester must see the conversation; editing it is the
        # permission check on create.
        ConversationManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=entity.conversation_id)

    def create(self, **kwargs: Any) -> Any:
        """Memberships with their grants: the same as adding participants.
        Every user named, singly or in a batch, must be one the requester
        can see, checked before any membership is written."""
        entities = kwargs.get("entities")
        named = entities if isinstance(entities, list) else [kwargs]
        for fields in named:
            user_id = dict(fields).get("user_id")
            if user_id:
                _visible_user(self.model_registry, self.requester.id, user_id)
        result = super().create(**kwargs)
        conversations = ConversationManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        )
        for membership in created_records(result):
            owner = conversations.get(id=membership.conversation_id).user_id
            if owner != membership.user_id:
                _grant(
                    self.model_registry, membership.conversation_id, membership.user_id
                )
        return result

    def delete(self, id: str) -> None:
        """Deleting a membership removes the participant, under the same
        rules (the owner removes anyone; a participant leaves)."""
        membership = self.get(id=id)
        ConversationManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).remove_participant(membership.conversation_id, membership.user_id)

    def delete_row(self, id: str) -> None:
        """Delete the membership row itself, once removal is authorized."""
        super().delete(id)


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
        None, description="The user who wrote the message; none for an agent"
    )

    table_comment: ClassVar[str] = (
        "Messages represent individual communications within conversations, "
        "supporting threading via parent_id and edit history tracking."
    )
    permission_references: ClassVar[List[str]] = ["conversation"]

    class Create(BaseModel, ConversationModel.Reference.ID, ParentMixinModel.Optional):
        content: str = Field(..., description="Content of the message")
        user_id: Optional[str] = Field(
            None, description="Set by the server: the poster, or none for an agent"
        )

    class Update(BaseModel):
        content: Optional[str] = Field(None, description="New content for the message")
        edited_at: Optional[datetime] = Field(None, description="Set by the server")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ParentMixinModel.Search,
        ConversationModel.Reference.ID.Search,
    ):
        content: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        is_deleted: Optional[bool] = None


class VoiceMessageRequest(RouteModel):
    conversation_id: str = Field(..., description="The conversation")
    audio_base64: str = Field(..., description="The recording, base64")
    filename: str = Field("audio.webm", description="The recording's file name")
    parent_id: Optional[str] = Field(None, description="The message replied to")


class ThreadResponse(RouteModel):
    messages: List[MessageModel]


class MessageManager(AbstractBLLManager, RouterMixin):
    _model = MessageModel

    prefix: ClassVar[Optional[str]] = "/v1/message"
    tags: ClassVar[Optional[List[str]]] = ["Messages"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    @property
    def feedbacks(self) -> "FeedbackManager":
        return FeedbackManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

    def create_validation(self, entity: Any) -> None:
        conversation = ConversationManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=entity.conversation_id)
        if entity.parent_id:
            parent = self.get(id=entity.parent_id)
            if parent.conversation_id != conversation.id:
                raise HTTPException(
                    status_code=400,
                    detail="A reply is in its parent's conversation",
                )

    def create(self, **kwargs: Any) -> Any:
        """Messages by the requester (ROOT and SYSTEM name the author, or
        none). Agent replies to new messages are the ``ai_agents``
        extension's create hook."""
        authored = owned_by(self.requester.id, server_may_leave_unowned=True)
        return super().create(**each_created(kwargs, authored))

    def create_agent_message(self, **kwargs: Any) -> Any:
        """A message an agent posts, with no author, into a conversation
        the requester (the agent's user) can post in."""
        kwargs.pop("entities", None)
        kwargs["user_id"] = None
        return super().create(**kwargs)

    def update(self, id: str, **kwargs: Any) -> Any:
        """Only the author edits a message; an edit is stamped."""
        message = self.get(id=id)
        if not server_side(self.requester.id) and message.user_id != self.requester.id:
            raise HTTPException(
                status_code=403, detail="Only the author edits a message"
            )
        kwargs.pop("edited_at", None)
        if "content" in kwargs:
            kwargs["edited_at"] = datetime.now(timezone.utc)
        return super().update(id, **kwargs)

    def delete(self, id: str) -> None:
        """The author or the conversation's owner deletes a message."""
        message = self.get(id=id)
        if server_side(self.requester.id) or message.user_id == self.requester.id:
            super().delete(id)
            return
        conversation = ConversationManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=message.conversation_id)
        if conversation.user_id != self.requester.id:
            raise HTTPException(
                status_code=403,
                detail="Only the author or the conversation's owner deletes a message",
            )
        # The database lets only a row's creator delete it; the owner's
        # moderation is authorized here, so the row goes as ROOT.
        MessageManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        ).delete(id)

    def get_thread(
        self, message_id: str, depth: int = MAX_THREAD_DEPTH
    ) -> List[MessageModel]:
        """A message and its replies, ``depth`` levels down (at most
        ``MAX_THREAD_DEPTH``), oldest first at each level."""
        depth = max(0, min(depth, MAX_THREAD_DEPTH))
        thread = [self.get(id=message_id)]
        level = [message_id]
        for _ in range(depth):
            replies: List[MessageModel] = []
            for parent_id in level:
                replies.extend(
                    self.list(
                        parent_id=parent_id, sort_by="created_at", sort_order="asc"
                    )
                )
            if not replies:
                break
            thread.extend(replies)
            if len(thread) >= MAX_THREAD_MESSAGES:
                return thread[:MAX_THREAD_MESSAGES]
            level = [reply.id for reply in replies]
        return thread

    def recent(self, conversation_id: str, limit: int) -> List[MessageModel]:
        """The last ``limit`` messages of a conversation, oldest first."""
        newest = self.list(
            conversation_id=conversation_id,
            sort_by="created_at",
            sort_order="desc",
            limit=limit,
        )
        return list(reversed(newest))

    @custom_route(
        method="GET",
        path="/{message_id}/thread",
        output_model=ThreadResponse,
        authentication_type="jwt",
        openapi_tags=("Messages",),
        summary="A message and its replies",
        expose_in=(ExposeIn.REST,),
    )
    def thread_route(
        self, message_id: str, depth: int = MAX_THREAD_DEPTH
    ) -> ThreadResponse:
        return ThreadResponse(messages=self.get_thread(message_id, depth))

    @custom_route(
        method="POST",
        path="/voice",
        input_model=VoiceMessageRequest,
        output_model=MessageModel,
        authentication_type="jwt",
        openapi_tags=("Messages",),
        summary="Post a recording as a message, transcribed by the AI extension",
        expose_in=(ExposeIn.REST,),
    )
    async def voice_route(self, body: VoiceMessageRequest) -> MessageModel:
        # The caller must be able to post before any audio leaves the server.
        ConversationManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=body.conversation_id)
        text = await _transcribe(body.audio_base64, body.filename)
        message: MessageModel = self.create(
            conversation_id=body.conversation_id,
            content=text,
            parent_id=body.parent_id,
        )
        return message


class FeedbackModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.ID.Optional,
    MessageModel.Reference,
    metaclass=ModelMeta,
):
    content: str = Field(..., description="Feedback content")
    positive: Optional[bool] = Field(
        None,
        description="Whether this is positive (True), negative (False), or neutral (None) feedback",
    )

    table_comment: ClassVar[str] = (
        "Feedback on messages from users, supporting positive/negative/neutral sentiment "
        "for message quality assessment and AI training."
    )
    permission_references: ClassVar[List[str]] = ["message"]

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

    prefix: ClassVar[Optional[str]] = "/v1/feedback"
    tags: ClassVar[Optional[List[str]]] = ["Feedback"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity: Any) -> None:
        MessageManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=entity.message_id)

    def create(self, **kwargs: Any) -> Any:
        return super().create(**each_created(kwargs, owned_by(self.requester.id)))

    def _own(self, id: str) -> None:
        if (
            not server_side(self.requester.id)
            and self.get(id=id).user_id != self.requester.id
        ):
            raise HTTPException(status_code=403, detail="Feedback is its author's")

    def update(self, id: str, **kwargs: Any) -> Any:
        self._own(id)
        return super().update(id, **kwargs)

    def delete(self, id: str) -> None:
        self._own(id)
        super().delete(id)


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

    table_comment: ClassVar[str] = (
        "Artifacts represent files and content attachments associated with conversations "
        "and messages, supporting encryption and various content types."
    )
    permission_references: ClassVar[List[str]] = ["conversation"]

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
        def validate_artifact_create(self) -> "ArtifactModel.Create":
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

    prefix: ClassVar[Optional[str]] = "/v1/artifact"
    tags: ClassVar[Optional[List[str]]] = ["Artifacts"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity: Any) -> None:
        ConversationManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=entity.conversation_id)

    def create(self, **kwargs: Any) -> Any:
        """Artifacts by the requester, each in its message's conversation."""
        authored = owned_by(self.requester.id)
        messages = MessageManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

        def placed(fields: Dict[str, Any]) -> Dict[str, Any]:
            fields = authored(fields)
            if fields.get("message_id"):
                message = messages.get(id=fields["message_id"])
                if fields.get("conversation_id") not in (None, message.conversation_id):
                    raise HTTPException(
                        status_code=400,
                        detail="An artifact is in its message's conversation",
                    )
                fields["conversation_id"] = message.conversation_id
            return fields

        return super().create(**each_created(kwargs, placed))


def _visible_user(model_registry: Any, requester_id: str, user_id: str) -> None:
    """A user named as a participant must be one the requester can see
    (the users rule: a shared live team hierarchy, either direction; ROOT
    and SYSTEM see everyone, which is how the server seats participants on
    its own account). An invisible user is a 404, as a missing one is, so
    a known id never reaches a stranger."""
    from zephyrex.logic.BLL_Auth import UserManager

    try:
        UserManager(requester_id=requester_id, model_registry=model_registry).get(
            id=user_id
        )
    except HTTPException:
        raise HTTPException(status_code=404, detail="User not found") from None


def _grant(model_registry: Any, conversation_id: str, user_id: str) -> None:
    """Give a participant view and edit on the conversation."""
    from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager

    PermissionManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    ).create(
        resource_type="conversations",
        resource_id=conversation_id,
        user_id=user_id,
        can_view=True,
        can_edit=True,
    )


def _revoke(model_registry: Any, conversation_id: str, user_id: str) -> None:
    from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager

    permissions = PermissionManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    for grant in permissions.list(
        resource_type="conversations", resource_id=conversation_id, user_id=user_id
    ):
        permissions.delete(id=grant.id)


async def _transcribe(audio_base64: str, filename: str) -> str:
    """The text of a recording, through the AI extension's transcription
    (503 when no transcribing model is configured)."""
    from zephyrex.extensions.ai.EXT_AI import EXT_AI

    transcript: Dict[str, Any] = await EXT_AI.transcribe(audio_base64, filename)
    text = str(transcript.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=422, detail="No speech was heard")
    return text


DirectMessageResponse.model_rebuild()
