# SPDX-License-Identifier: AGPL-3.0-or-later
"""Identity/auth provider contract for the code-generation engine.

The generation engine (``lib/Pydantic2FastAPI``, ``lib/Pydantic2Strawberry``,
``lib/Pydantic``) needs to authenticate requests and resolve users when it emits
FastAPI/Strawberry auth dependencies and include-expansion handlers. It must do
so **without importing a concrete identity model from** ``logic/`` — otherwise
``lib/`` cannot be extracted as a standalone package and the identity model is
not swappable (issue #221).

This module inverts that dependency: ``lib/`` depends only on this contract, and
the concrete identity/auth manager registers itself here at import/boot. The
default ``logic.BLL_Auth.UserManager`` registers on import; a consumer can
register an alternative manager exposing the same surface without editing
``lib/``.

Required provider surface (duck-typed — any object/class with these works):
- ``auth(*, model_registry, authorization, request) -> user`` (classmethod/static)
- ``verify_token(*, token, model_registry) -> payload`` (classmethod/static)
- ``__call__(*, requester_id, model_registry) -> manager`` (i.e. it is
  constructable, so a manager instance can be built to fetch user objects)
"""

from typing import Any, Optional

_auth_provider: Optional[Any] = None


def register_auth_provider(provider: Any) -> None:
    """Register the concrete identity/auth manager used by the generation engine.

    Called at import/boot by the identity extension (``logic.BLL_Auth`` does this
    for the default ``UserManager``). Registering a different provider swaps the
    identity manager the generated auth dependencies use, with no edits to
    ``lib/``.
    """
    global _auth_provider
    _auth_provider = provider


def get_auth_provider() -> Any:
    """Return the registered identity/auth manager.

    Raises ``RuntimeError`` with a clear message if nothing is registered, so a
    misconfigured deployment fails loudly rather than silently skipping auth.
    """
    if _auth_provider is None:
        raise RuntimeError(
            "No auth provider registered. The identity/auth manager must call "
            "register_auth_provider(...) at import/boot — logic.BLL_Auth does this "
            "for the default UserManager. Import the identity extension, or "
            "register a compatible provider, before serving authenticated routes."
        )
    return _auth_provider
