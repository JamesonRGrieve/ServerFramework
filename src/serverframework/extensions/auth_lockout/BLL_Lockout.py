"""FailedLoginAttempt entity, manager, and per-user threshold gate.

Extracted from `logic/BLL_Auth.py` (Scope #2). The IP-keyed in-memory
`LockoutTracker` stays in core; this extension is the durable record + the
per-user count gate.
"""

from datetime import datetime, timedelta, timezone
from typing import ClassVar, List, Optional, Type

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
)
from serverframework.logic.BLL_Auth import UserModel


class FailedLoginAttemptModel(
    ApplicationModel.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["FailedLoginAttemptManager"]] = None
    ip_address: Optional[str] = Field(
        None, description="IP address of failed login attempt"
    )

    table_comment: ClassVar[str] = (
        "Records of failed login attempts for security monitoring and lockout enforcement"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        ip_address: Optional[str] = Field(
            None, description="IP address of the failed login attempt"
        )

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        ip_address: Optional[StringSearchModel] = None
        created_at: Optional[DateSearchModel] = None


class FailedLoginAttemptManager(AbstractBLLManager, RouterMixin):
    _model = FailedLoginAttemptModel
    prefix: ClassVar[Optional[str]] = "/v1/admin/failed-logins"
    tags: ClassVar[Optional[List[str]]] = ["Account Lockout"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    # Per-user threshold. Configurable here; the IP-keyed lockout tracker in
    # `lib/InboundSecurity` enforces a separate per-IP gate.
    MAX_FAILED_ATTEMPTS_PER_HOUR: ClassVar[int] = 5

    def _register_search_transformers(self):
        self.register_search_transformer("recent", self._transform_recent_search)

    def _transform_recent_search(self, hours):
        if not hours or not isinstance(hours, int):
            hours = 1
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [
            FailedLoginAttemptModel.DB(self.model_registry.DB.manager.Base).created_at
            >= cutoff_time
        ]

    def count_recent(self, user_id: str, hours: int = 1) -> int:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        return FailedLoginAttemptModel.DB(self.model_registry.DB.manager.Base).count(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            user_id=user_id,
            filters=[
                FailedLoginAttemptModel.DB(
                    self.model_registry.DB.manager.Base
                ).created_at
                >= cutoff_time
            ],
        )

    def assert_user_within_threshold(self, user_id: str, model_registry) -> None:
        """Called from `UserManager.login` (via the registered hook) to gate
        further work on the per-user threshold."""
        count = FailedLoginAttemptModel.DB(model_registry.DB.manager.Base).count(
            requester_id=user_id,
            model_registry=model_registry,
            user_id=user_id,
            filters=[
                FailedLoginAttemptModel.DB(model_registry.DB.manager.Base).created_at
                >= datetime.now(timezone.utc) - timedelta(hours=1)
            ],
        )
        if count >= self.MAX_FAILED_ATTEMPTS_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Please try again later.",
            )

    def record_failure(
        self, user_id: str, ip_address: Optional[str], model_registry
    ) -> None:
        FailedLoginAttemptModel.DB(model_registry.DB.manager.Base).create(
            requester_id=user_id,
            model_registry=model_registry,
            user_id=user_id,
            ip_address=ip_address,
        )


FailedLoginAttemptModel.Manager = FailedLoginAttemptManager


# ---------------------------------------------------------------------------
# Hook registration (Scope #2).
# ---------------------------------------------------------------------------


def _assert_within_threshold(user_id, model_registry):
    from serverframework.lib.Environment import env

    mgr = FailedLoginAttemptManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    mgr.assert_user_within_threshold(user_id, model_registry)


def _record_failure(user_id, ip_address, model_registry):
    from serverframework.lib.Environment import env

    mgr = FailedLoginAttemptManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    mgr.record_failure(user_id, ip_address, model_registry)


try:
    from serverframework.logic.BLL_Auth import register_lockout_hooks

    register_lockout_hooks(
        assert_within_threshold=_assert_within_threshold,
        record_failure=_record_failure,
    )
except ImportError:
    pass
