# SPDX-License-Identifier: AGPL-3.0-or-later
"""Process-global hook registries consumed by the lib/ layer.

These live here — at/below their ``lib/`` consumers — rather than in
``logic/BLL_Auth`` (a layer above), so the generation/logging layer never
imports upward into ``logic/`` to read a hook (issue #221; the same inversion as
issue #222's ACL/invitation hooks). The privacy and federation extensions
register their concrete callables via ``register_pii_hooks`` /
``register_registry_hooks`` at ``on_load``; ``logic/BLL_Auth`` re-exports these
names for backward compatibility, and the dict objects are shared by identity so
registrations remain visible through either import path.
"""

# privacy extension hooks — populated by `privacy.on_load`. Core's logging
# redaction (``lib/Logging``, ``lib/Credentials``) consults the registered
# ``log_filter`` callable on every record. Without the extension, only the
# registered-secret scrubbing in core fires.
_pii_hooks: dict = {
    "log_filter": None,  # (logging.LogRecord) -> bool
}


def register_pii_hooks(*, log_filter=None) -> None:
    if log_filter is not None:
        _pii_hooks["log_filter"] = log_filter


# registry generation hooks — populated by extensions' `on_load`. Core
# ``ModelRegistry.commit`` dispatches through these after Phase 1 model binding
# so extensions can participate in the code-generation pipeline without core
# importing them. Each is a no-op when its owning extension is absent.
#
# - ``bootstrap_federation`` (``federation`` ext): lift external types into the
#   registry so the framework serves a federated schema.
# - ``generate_sdk`` (``meta_sdk_<lang>`` exts): a language -> generator map so a
#   single committed registry can emit typed client SDKs in several languages.
#   Each ``meta_sdk_py`` / ``meta_sdk_ts`` / ``meta_sdk_rs`` extension registers
#   its emitter via ``register_sdk_generator`` at on_load. Opt-in and idempotent
#   by language — nothing generates unless a language extension is enabled.
_registry_hooks: dict = {
    "bootstrap_federation": None,  # (model_registry) -> federation_report | None
    "generate_sdk": {},  # {language: (model_registry) -> None}
}


def register_registry_hooks(*, bootstrap_federation=None) -> None:
    if bootstrap_federation is not None:
        _registry_hooks["bootstrap_federation"] = bootstrap_federation


def register_sdk_generator(language: str, generator) -> None:
    """Register an opt-in SDK generator for ``language`` (idempotent by language).

    Each ``meta_sdk_<language>`` extension registers its emitter here at on_load;
    ``ModelRegistry.commit`` then invokes every registered generator, so enabling
    ``meta_sdk_py`` + ``meta_sdk_ts`` produces both SDKs from the one registry.
    Keying by language makes re-registration (repeated server boots under a test
    suite) overwrite rather than accumulate, so no generator fires twice.
    """
    _registry_hooks["generate_sdk"][language] = generator
