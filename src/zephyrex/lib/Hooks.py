# SPDX-License-Identifier: AGPL-3.0-or-later
"""Process-global hook registries consumed by the lib/ layer.

These live here — at/below their ``lib/`` consumers — rather than in
``logic/BLL_Auth`` (a layer above), so the generation/logging layer never
imports upward into ``logic/`` to read a hook (issue #221; the same inversion as
issue #222's ACL/invitation hooks). The privacy and federation extensions
register their concrete callables via ``register_pii_hooks`` /
``register_registry_hooks`` at ``on_initialize``; ``logic/BLL_Auth`` re-exports these
names for backward compatibility, and the dict objects are shared by identity so
registrations remain visible through either import path.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

# privacy extension hooks — populated by `privacy.on_initialize`. Core's logging
# redaction (``lib/Logging``, ``lib/Credentials``) consults the registered
# ``log_filter`` callable on every record. Without the extension, only the
# registered-secret scrubbing in core fires.
_pii_hooks: dict = {
    "log_filter": None,  # (logging.LogRecord) -> bool
}


def register_pii_hooks(*, log_filter=None) -> None:
    if log_filter is not None:
        _pii_hooks["log_filter"] = log_filter


# registry generation hooks — populated by extensions' `on_initialize`. Core
# ``ModelRegistry.commit`` dispatches through these after Phase 1 model binding
# so extensions can participate in the code-generation pipeline without core
# importing them. Each is a no-op when its owning extension is absent.
#
# - ``bootstrap_federation`` (``federation`` ext): lift external types into the
#   registry so the framework serves a federated schema.
# - ``generate_sdk`` (``meta_sdk_<lang>`` exts): an extension-name -> generator
#   map so a single committed registry can emit typed client SDKs in several
#   languages. Each ``meta_sdk_py`` / ``meta_sdk_ts`` / ``meta_sdk_rs`` extension
#   registers its emitter via ``register_sdk_generator`` at on_initialize.
#   Keyed by the owning extension so a commit runs only the generators of the
#   extensions its own app loaded (``sdk_generators_for``).
_registry_hooks: dict = {
    "bootstrap_federation": None,  # (model_registry) -> federation_report | None
    "generate_sdk": {},  # {extension_name: SDKTarget}
}


def register_registry_hooks(*, bootstrap_federation=None) -> None:
    if bootstrap_federation is not None:
        _registry_hooks["bootstrap_federation"] = bootstrap_federation


@dataclass(frozen=True)
class SDKTarget:
    """One ``meta_sdk_<language>`` extension's SDK: the generator, the
    language it emits, and the environment variable naming the directory it
    writes to (unset: the generator does nothing)."""

    language: str
    output_dir_env: str
    generator: Any


def register_sdk_generator(
    extension_name: str, generator, *, language: str, output_dir_env: str
) -> None:
    """Register the SDK generator owned by ``extension_name`` (idempotent).

    Each ``meta_sdk_<language>`` extension registers its emitter here at
    on_initialize. Keying by extension makes re-registration (every app build
    under a test suite) overwrite rather than accumulate, so no generator fires
    twice.
    """
    _registry_hooks["generate_sdk"][extension_name] = SDKTarget(
        language=language, output_dir_env=output_dir_env, generator=generator
    )


def sdk_targets_for(extension_names: Collection[str]) -> dict[str, SDKTarget]:
    """The registered SDKs owned by ``extension_names``, by extension.

    ``ModelRegistry.commit`` passes the extensions loaded into the registry it
    commits, so enabling ``meta_sdk_py`` + ``meta_sdk_ts`` in one app produces
    both SDKs while a sibling app in the same process that loaded neither
    produces none; the SDK download routes list the same set.
    """
    return {
        name: target
        for name, target in _registry_hooks["generate_sdk"].items()
        if name in extension_names
    }


def sdk_generators_for(extension_names: Collection[str]) -> list[tuple[str, Any]]:
    """The registered SDK generators owned by ``extension_names``."""
    return [
        (name, target.generator)
        for name, target in sdk_targets_for(extension_names).items()
    ]
