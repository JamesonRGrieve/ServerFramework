# SPDX-License-Identifier: AGPL-3.0-or-later
"""Right to erasure on account deletion.

Extensions register an eraser for the data they hold about a user
(``register_eraser``). With the privacy extension enabled, an account
deleting itself (``DELETE /v1/user``) first runs every eraser: after the
request's If-Match on the account has been checked, so a stale request
erases nothing, and before the account goes, so a failed eraser stops the
deletion and the user can simply try again (erasers are idempotent).
"""

from typing import Any, Callable, Dict, Set

from fastapi import HTTPException

from zephyrex.extensions.privacy.BLL_PII import ErasureOrchestrator, ErasureReport
from zephyrex.lib.Logging import logger

# name -> eraser(user_id, model_registry), in registration order.
_ERASERS: Dict[str, Callable[[str, Any], None]] = {}
# The erasers that sign the account out (its sessions): they run only once
# every other has succeeded, so an account kept after a failed erasure stays
# signed in to try again.
_SIGNING_OUT: Set[str] = set()

_HOOKED = False


def register_eraser(
    name: str, fn: Callable[[str, Any], None], *, signs_out: bool = False
) -> None:
    """Erase extension ``name``'s data about a user when their account is
    deleted, in an app that loaded ``name``.

    ``fn(user_id, model_registry)`` must be idempotent and runs as part of
    the deletion; erasers run in reverse registration order, so an
    extension that depends on another (and so registers after it) erases
    first. One that ``signs_out`` (ends the account's sessions) runs after
    all the others, and only when they all succeeded."""
    _ERASERS[name] = fn
    if signs_out:
        _SIGNING_OUT.add(name)
    else:
        _SIGNING_OUT.discard(name)


    """The account's own credentials (its password hash), which the core
    keeps and no extension owns: deleted with the account. Safe to run twice."""
    from zephyrex.lib.Environment import env
    from zephyrex.logic.BLL_Auth.user import UserCredentialModel

    CredentialDB = UserCredentialModel.DB(model_registry.DB.manager.Base)
    rows = (
        CredentialDB.list(
            model_registry=model_registry,
            filters=[
                CredentialDB.user_id == user_id,
            ],
            return_type="dto",
            override_dto=UserCredentialModel,
        )
        or []
    )
    for row in rows:
        CredentialDB.delete(
        )


    """Run, for ``user_id``, the erasers of the extensions loaded into
    ``model_registry``'s app (a registration is process-wide; another app in
    the process may not have loaded the extension). When they all succeed,
    the account's sign-in records go last: the erasers that sign it out,
    then its credentials."""
    loaded = model_registry.loaded_extension_names()
    data, sign_in = ErasureOrchestrator(), ErasureOrchestrator()
    sign_in.register_extension_eraser(
        "credentials", _bound(erase_credentials, model_registry)
    )
    for name, fn in _ERASERS.items():
        if name in loaded:
            run = sign_in if name in _SIGNING_OUT else data
            run.register_extension_eraser(name, _bound(fn, model_registry))
    report = data.erase_user(user_id)
    if not report.succeeded:
        return report
    last = sign_in.erase_user(user_id)
    report.erased_extensions.extend(last.erased_extensions)
    report.failed_extensions.update(last.failed_extensions)
    return report


def _bound(
    fn: Callable[[str, Any], None], model_registry: Any
) -> Callable[[str], None]:
    """``fn`` for one app: the orchestrator calls erasers with the user id alone."""

    def erase(user_id: str) -> None:
        fn(user_id, model_registry)

    return erase


    from zephyrex.lib.Preconditions import check_route_record

    manager = context.manager
    me = str(manager.requester.id)
    target = (
        context.kwargs.get("id") or (context.args[0] if context.args else None) or me
    )
    if str(target) != me:
        return
    check_route_record(manager, me)
    report = erase_user(me, manager.model_registry)
    if not report.succeeded:
        logger.error("Erasure failed for %s: %s", me, report.failed_extensions)
        raise HTTPException(
            status_code=500,
            detail="Your data could not all be erased, so your account was kept. Try again.",
        )


def hook_account_deletion() -> None:
    """Run the erasers before an account deletes itself (once per process)."""
    global _HOOKED
    if _HOOKED:
        return
    from zephyrex.logic.AbstractLogicManager import HookTiming, hook_bll
    from zephyrex.logic.BLL_Auth import UserManager

    hook_bll(UserManager.delete, timing=HookTiming.BEFORE, priority=10)(
        _erase_before_deleting
    )
    _HOOKED = True
