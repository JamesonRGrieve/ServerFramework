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


# federation extension hooks — populated by `federation.on_load`. Core
# ``ModelRegistry.commit`` calls ``bootstrap`` after Phase 1 model binding so the
# federation extension can lift external types into the registry. Without it, the
# framework runs as a single-app server (no upstream federation) and the hook is
# a no-op.
_registry_hooks: dict = {
    "bootstrap_federation": None,  # (model_registry) -> federation_report | None
}


def register_registry_hooks(*, bootstrap_federation=None) -> None:
    if bootstrap_federation is not None:
        _registry_hooks["bootstrap_federation"] = bootstrap_federation
