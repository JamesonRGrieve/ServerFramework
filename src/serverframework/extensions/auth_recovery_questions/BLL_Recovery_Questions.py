"""Recovery question entity, manager, and verify helpers.

Extracted from `logic/BLL_Auth.py` (Scope #1). Apps that do not want
question-based recovery simply omit ``auth_recovery_questions`` from
APP_EXTENSIONS.
"""

import unicodedata
from datetime import datetime
from typing import ClassVar, List, Optional, Type

import bcrypt
from fastapi import HTTPException
from pydantic import Field

from serverframework.lib.Pydantic import BaseModel
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import _BCRYPT_ROUNDS, UserModel


def _normalize_answer(answer: str) -> str:
    """Normalize a recovery-question answer for hashing/comparison.

    Steps:
    1. NFKC Unicode normalization (folds compatibility variants and composes).
    2. Casefold (locale-insensitive lower-case; handles e.g. German ``ß`` → ``ss``).
    3. Collapse runs of any whitespace (incl. NBSP, ZWJ, etc.) to a single space.
    4. Strip leading/trailing whitespace.

    NFKC alone does not handle every confusable; this is best-effort to make
    a user's answer recoverable across keyboards/input methods without
    weakening security against an attacker who already has the hash.
    """
    if answer is None:
        return ""
    normalized = unicodedata.normalize("NFKC", answer)
    normalized = normalized.casefold()
    normalized = " ".join(normalized.split())
    return normalized


class UserRecoveryQuestionModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserRecoveryQuestionManager"]] = None
    question: str = Field(..., description="Recovery question")
    answer: str = Field(..., description="Hashed answer to recovery question")

    table_comment: ClassVar[str] = (
        "Security questions for account recovery when a user forgets their password"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        question: str = Field(..., description="Recovery question")
        answer: str = Field(
            ..., description="Answer to recovery question (will be hashed)"
        )

    class Update(BaseModel):
        question: Optional[str] = Field(None, description="Recovery question")
        answer: Optional[str] = Field(
            None, description="Answer to recovery question (will be hashed)"
        )

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        question: Optional[StringSearchModel] = None


class UserRecoveryQuestionManager(AbstractBLLManager, RouterMixin):
    _model = UserRecoveryQuestionModel
    prefix: ClassVar[Optional[str]] = "/v1/user/recovery-questions"
    tags: ClassVar[Optional[List[str]]] = ["Account Recovery"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # A user may only set their own recovery questions; ROOT bypasses.
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)

    def create(self, **kwargs):
        if "answer" in kwargs:
            answer = kwargs.pop("answer")
            normalized_answer = _normalize_answer(answer)
            salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
            kwargs["answer"] = bcrypt.hashpw(normalized_answer.encode(), salt).decode()
        return super().create(**kwargs)

    def update(self, id: str, **kwargs):
        if "answer" in kwargs:
            answer = kwargs.pop("answer")
            normalized_answer = _normalize_answer(answer)
            salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
            kwargs["answer"] = bcrypt.hashpw(normalized_answer.encode(), salt).decode()
        return super().update(id, **kwargs)

    def verify_answer(self, question_id: str, answer: str) -> bool:
        question = UserRecoveryQuestionModel.DB(
            self.model_registry.DB.manager.Base
        ).get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=question_id,
        )
        if not question:
            return False
        normalized_answer = _normalize_answer(answer)
        return bcrypt.checkpw(normalized_answer.encode(), question.answer.encode())


UserRecoveryQuestionModel.Manager = UserRecoveryQuestionManager


# ---------------------------------------------------------------------------
# Merge participation
# ---------------------------------------------------------------------------


def _merge_handler(ctx) -> None:
    """Delete the target user's recovery questions.

    Recovery answers are personal: migrating them onto the surviving
    account would mean the initiating user could pass recovery checks for
    secrets that were originally answered by the target user. The
    initiating user's own recovery questions stay; this only clears the
    target's set."""
    from serverframework.lib.Environment import env as _env

    QDB = UserRecoveryQuestionModel.DB(ctx.model_registry.DB.manager.Base)
    rows = (
        QDB.list(
            requester_id=_env("ROOT_ID"),
            model_registry=ctx.model_registry,
            filters=[QDB.user_id == ctx.target_user_id],
            return_type="dto",
            override_dto=UserRecoveryQuestionModel,
        )
        or []
    )
    for row in rows:
        QDB.delete(
            requester_id=_env("ROOT_ID"),
            model_registry=ctx.model_registry,
            id=row.id,
        )


def register_merge_participation() -> None:
    try:
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            register_merge_handler,
        )
    except ImportError:
        return
    register_merge_handler("auth_recovery_questions", _merge_handler)
