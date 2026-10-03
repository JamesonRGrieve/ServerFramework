# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stored prompts and their arguments.

A prompt's content marks variables as ``{VARIABLE_NAME}`` (capitals, digits
and underscores). Each argument names one, optionally with a default;
building a prompt fills its variables from the values given, then the
defaults, and reports the variables still missing.

A prompt belongs to whoever creates it (ROOT and SYSTEM may name another
owner); ownership does not change on update. Arguments inherit access from
their prompt, so only someone who may edit a prompt adds or changes its
arguments.
"""

import re
from typing import Any, Callable, ClassVar, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DescriptionMixinModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

MAX_PROMPT_CHARACTERS = 200_000
VARIABLE = re.compile(r"\{([A-Z_][A-Z0-9_]*)\}")


def variables_in(content: str) -> List[str]:
    """The variable names ``content`` uses, in order of first use."""
    return list(dict.fromkeys(VARIABLE.findall(content)))


def fill(content: str, values: Dict[str, Any]) -> str:
    """``content`` with each ``{VARIABLE}`` it has a value for replaced."""
    return VARIABLE.sub(
        lambda match: (
            str(values[match.group(1)]) if match.group(1) in values else match.group(0)
        ),
        content,
    )


def _owned_by(requester_id: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
        server_side = is_root_id(requester_id) or is_system_id(requester_id)
        if not server_side or not fields.get("user_id"):
            fields["user_id"] = requester_id
        return fields

    return prepare


def _each(
    kwargs: Dict[str, Any], prepare: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Any]:
    if isinstance(kwargs.get("entities"), list):
        return {**kwargs, "entities": [prepare(dict(e)) for e in kwargs["entities"]]}
    return prepare(dict(kwargs))


class PromptModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    favourite: bool = Field(False, description="Marked as a favourite")
    content: str = Field(
        ...,
        description="The prompt, with {VARIABLE_NAME} placeholders",
        max_length=MAX_PROMPT_CHARACTERS,
    )

    table_comment: ClassVar[str] = (
        "A Prompt represents a stored prompt that can be used in Conversations and elsewhere. "
        "Variable injection for prompts is expected in the form {VARIABLE_NAME}."
    )
    is_system_entity: ClassVar[bool] = False

    class Create(
        BaseModel,
        NameMixinModel,
        DescriptionMixinModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        favourite: bool = Field(False, description="Marked as a favourite")
        content: str = Field(
            ..., description="The prompt", max_length=MAX_PROMPT_CHARACTERS
        )

    class Update(BaseModel, NameMixinModel.Optional, DescriptionMixinModel.Optional):
        favourite: Optional[bool] = Field(None, description="Marked as a favourite")
        content: Optional[str] = Field(
            None, description="The prompt", max_length=MAX_PROMPT_CHARACTERS
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        DescriptionMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        favourite: Optional[bool] = Field(None, description="Filter by favourite")
        content: Optional[StringSearchModel] = None


class BuildRequest(RouteModel):
    variables: Dict[str, str] = Field(
        default_factory=dict, description="Values for the prompt's variables"
    )


class BuiltPrompt(RouteModel):
    text: str
    missing: List[str] = Field(
        description="Variables with neither a value nor a default, left as written"
    )


class PromptManager(AbstractBLLManager, RouterMixin):
    _model = PromptModel

    prefix: ClassVar[Optional[str]] = "/v1/prompt"
    tags: ClassVar[Optional[List[str]]] = ["Prompt Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    @property
    def arguments(self) -> "PromptArgumentManager":
        return PromptArgumentManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

    def create(self, **kwargs: Any) -> Any:
        """Prompts owned by the requester (ROOT and SYSTEM may name another)."""
        return super().create(**_each(kwargs, _owned_by(self.requester.id)))

    def update(self, id: str, **kwargs: Any) -> Any:
        """A prompt's owner and team are not changed by an update."""
        kwargs.pop("user_id", None)
        kwargs.pop("team_id", None)
        return super().update(id, **kwargs)

    def defaults(self, prompt_id: str) -> Dict[str, Optional[str]]:
        """Each of the prompt's arguments and its default (None: required)."""
        return {
            argument.name: argument.default_value
            for argument in self.arguments.list(prompt_id=prompt_id)
        }

    def build(self, prompt_id: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """The prompt filled from ``variables``, then its arguments'
        defaults: ``{text, missing}``."""
        prompt = self.get(id=prompt_id)
        values = {
            name: default
            for name, default in self.defaults(prompt_id).items()
            if default is not None
        }
        values.update(variables)
        return {
            "text": fill(prompt.content, values),
            "missing": [v for v in variables_in(prompt.content) if v not in values],
        }

    def sync_arguments(self, prompt_id: str) -> List[str]:
        """Add an argument for each variable the content uses and lacks one;
        the names added."""
        prompt = self.get(id=prompt_id)
        existing = set(self.defaults(prompt_id))
        added = [v for v in variables_in(prompt.content) if v not in existing]
        for name in added:
            self.arguments.create(prompt_id=prompt_id, name=name, default_value=None)
        return added

    @custom_route(
        method="POST",
        path="/{prompt_id}/build",
        input_model=BuildRequest,
        output_model=BuiltPrompt,
        authentication_type="jwt",
        openapi_tags=("Prompt Management",),
        summary="Fill a prompt's variables",
        expose_in=(ExposeIn.REST,),
    )
    def build_route(self, prompt_id: str, body: BuildRequest) -> BuiltPrompt:
        return BuiltPrompt(**self.build(prompt_id, body.variables))


class PromptArgumentModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    PromptModel.Reference.ID,
    metaclass=ModelMeta,
):
    default_value: Optional[str] = Field(
        None, description="Value used when none is given; none means required"
    )

    table_comment: ClassVar[str] = (
        "A PromptArgument represents a variable injection for a Prompt."
    )
    is_system_entity: ClassVar[bool] = False
    permission_references: ClassVar[List[str]] = ["prompt"]

    class Create(BaseModel, NameMixinModel, PromptModel.Reference.ID):
        default_value: Optional[str] = Field(None, description="Default value")

    class Update(BaseModel, NameMixinModel.Optional):
        default_value: Optional[str] = Field(None, description="Default value")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        NameMixinModel.Search,
        PromptModel.Reference.ID.Search,
    ):
        default_value: Optional[StringSearchModel] = None


class PromptArgumentManager(AbstractBLLManager, RouterMixin):
    _model = PromptArgumentModel

    prefix: ClassVar[Optional[str]] = "/v1/prompt-argument"
    tags: ClassVar[Optional[List[str]]] = ["Prompt Argument Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity: Any) -> None:
        if not VARIABLE.fullmatch("{" + str(entity.name) + "}"):
            raise HTTPException(
                status_code=422,
                detail="An argument's name is a variable name (A-Z, 0-9, _)",
            )
        PromptManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=entity.prompt_id)


# Messages record the prompt they were generated from, when the
# conversations extension is present.
try:
    from zephyrex.extensions.conversations.BLL_Conversations import MessageModel
    from zephyrex.pydantic2.sqlalchemy import extension_model

    @extension_model(MessageModel)
    class MessagePromptExtension(RouteModel):
        prompt_id: Optional[str] = Field(
            None, description="ID of the prompt used to generate this message"
        )

except ImportError:
    pass
