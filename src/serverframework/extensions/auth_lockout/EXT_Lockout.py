"""auth_lockout extension definition.

Registers the `FailedLoginAttempt` model and wires `UserManager.login` hooks
that consult the per-user threshold + record failures into the durable table.
"""

from typing import ClassVar, List, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


def _assert_within_threshold(user_id, model_registry):
    from serverframework.extensions.auth_lockout.BLL_Lockout import (
        FailedLoginAttemptManager,
    )
    from serverframework.lib.Environment import env

    mgr = FailedLoginAttemptManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    mgr.assert_user_within_threshold(user_id, model_registry)


def _record_failure(user_id, ip_address, model_registry):
    from serverframework.extensions.auth_lockout.BLL_Lockout import (
        FailedLoginAttemptManager,
    )
    from serverframework.lib.Environment import env

    mgr = FailedLoginAttemptManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    mgr.record_failure(user_id, ip_address, model_registry)


class AuthLockoutExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_lockout"
    description: ClassVar[str] = (
        "Persisted failed-login records and per-user lockout policy"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.extensions.auth_lockout.BLL_Lockout import (
            FailedLoginAttemptModel,
        )

        return [FailedLoginAttemptModel]

    @classmethod
    def on_load(cls) -> None:
        from serverframework.logic.BLL_Auth import register_lockout_hooks

        register_lockout_hooks(
            assert_within_threshold=_assert_within_threshold,
            record_failure=_record_failure,
        )
